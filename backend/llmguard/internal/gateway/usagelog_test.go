package gateway

// Tests for the ClickHouse usage log.
//
// These state an intended CONTRACT rather than characterizing existing behavior
// — the same exception admission_test.go, breakershare_test.go and tracing_test.go
// take, and for the same reason: the code is new, so there is no prior conduct to
// preserve. What they pin is the one property that makes a billing sink safe to
// put behind a request path: it may lose rows, but it may never block a caller,
// grow without bound, or take the process down.
//
// Every buffer test asserts through a FAKE SINK rather than a live ClickHouse.
// That is deliberate: the interesting logic — when a flush fires, what happens at
// the ceiling, what shutdown drains — is all in usagelog.go and none of it is in
// the driver. The one thing a fake cannot prove is that a row survives the wire
// format with its types intact, which is exactly what the integration test at the
// bottom is for.

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/shopspring/decimal"
	"go.opentelemetry.io/otel/trace"

	"documedai/llmguard/internal/testutil"
)

// --- fake sink ---

// fakeSink records the batches handed to it and can be made to block or fail.
type fakeSink struct {
	mu      sync.Mutex
	batches [][]UsageRow

	// block, when non-nil, is waited on inside insert — used to wedge the flusher
	// so the buffer fills and Write reaches its overflow branch.
	block chan struct{}
	// err, when non-nil, is returned by every insert.
	err error
}

func (f *fakeSink) insert(_ context.Context, rows []UsageRow) error {
	if f.block != nil {
		<-f.block
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	// Copied because the writer reuses the batch slice after a flush; storing the
	// slice itself would leave every recorded batch aliasing the same array.
	stored := make([]UsageRow, len(rows))
	copy(stored, rows)
	f.batches = append(f.batches, stored)
	return f.err
}

func (f *fakeSink) close() error { return nil }

func (f *fakeSink) rowCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := 0
	for _, b := range f.batches {
		n += len(b)
	}
	return n
}

func (f *fakeSink) batchSizes() []int {
	f.mu.Lock()
	defer f.mu.Unlock()
	sizes := make([]int, 0, len(f.batches))
	for _, b := range f.batches {
		sizes = append(sizes, len(b))
	}
	return sizes
}

// usageTestConfig builds a Config carrying only the usage knobs.
//
// Written out rather than taken from realDefaults() because the harness
// deliberately does not mirror these (see config_test.go): every test here wants
// a batch size and interval chosen for what it is asserting, not production's.
func usageTestConfig(bufSize, batchSize int, interval time.Duration) Config {
	return Config{
		UsageBufferSize:    bufSize,
		UsageBatchSize:     batchSize,
		UsageFlushInterval: interval,
	}
}

func newTestWriter(t *testing.T, cfg Config, sink usageSink) (*UsageWriter, *Metrics) {
	t.Helper()
	m := newMetricsWith(prometheus.NewRegistry())
	w := newUsageWriterWithSink(cfg, sink, m, slog.New(slog.NewTextHandler(io.Discard, nil)))
	// Every test must stop its flusher, or the goroutine outlives the test and the
	// race detector reports it against whatever runs next.
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = w.Close(ctx)
	})
	return w, m
}

func sampleRow(id string) UsageRow {
	return UsageRow{
		TS:               time.Now().UTC(),
		RequestID:        id,
		Model:            "gemini-2.5-flash",
		Provider:         "mock",
		PromptTokens:     11,
		CompletionTokens: 22,
		CostUSD:          decimal.RequireFromString("0.00007500"),
		LatencyMS:        1234,
		UpstreamStatus:   200,
		Retries:          1,
		DedupHit:         true,
		APIKeyHint:       "abcdef",
	}
}

// --- flush triggers ---

// Flush-by-size caps how large one insert gets. With an interval long enough that
// it cannot be the cause, reaching the batch size is the only thing that can have
// produced a flush.
func TestUsageWriterFlushesBySize(t *testing.T) {
	sink := &fakeSink{}
	w, _ := newTestWriter(t, usageTestConfig(100, 3, time.Hour), sink)

	for i := range 3 {
		w.Write(sampleRow(fmt.Sprintf("r%d", i)))
	}

	testutil.RequireEventually(t, 2*time.Second, 5*time.Millisecond,
		func() bool { return sink.rowCount() == 3 },
		"3 rows should flush as soon as the batch size is reached")

	if got := sink.batchSizes(); len(got) != 1 || got[0] != 3 {
		t.Errorf("batch sizes = %v, want exactly one batch of 3", got)
	}
}

// Flush-by-interval bounds STALENESS, which is a different quantity from batch
// size and the only thing that gets a row out at low traffic. One row against a
// batch size of 1000 can only have been flushed by the ticker.
func TestUsageWriterFlushesByInterval(t *testing.T) {
	sink := &fakeSink{}
	w, _ := newTestWriter(t, usageTestConfig(100, 1000, 20*time.Millisecond), sink)

	w.Write(sampleRow("solo"))

	testutil.RequireEventually(t, 2*time.Second, 5*time.Millisecond,
		func() bool { return sink.rowCount() == 1 },
		"a single row should be flushed by the interval, not left in memory")
}

// A partial batch left over when the interval fires must not be dropped or
// double-written: the size trigger takes the full batches, the ticker takes the
// remainder, and together they account for every row exactly once.
func TestUsageWriterAccountsForEveryRowAcrossBothTriggers(t *testing.T) {
	const rows = 25
	sink := &fakeSink{}
	w, m := newTestWriter(t, usageTestConfig(100, 10, 20*time.Millisecond), sink)

	for i := range rows {
		w.Write(sampleRow(fmt.Sprintf("r%d", i)))
	}

	testutil.RequireEventually(t, 2*time.Second, 5*time.Millisecond,
		func() bool { return sink.rowCount() == rows },
		"every row should reach the sink exactly once")

	if v := testutil.CounterValue(t, m.usageRows); v != rows {
		t.Errorf("usage_rows_written_total = %v, want %d", v, rows)
	}
	if v := testutil.CounterValue(t, m.usageDropped); v != 0 {
		t.Errorf("usage_rows_dropped_total = %v, want 0 — nothing should be dropped below the ceiling", v)
	}
}

// --- the ceiling ---

// The bounded buffer is the whole safety property. Past capacity a row is DROPPED
// AND COUNTED, never queued: an unbounded buffer would turn a ClickHouse outage
// into the gateway's own OOM, which is the failure this design exists to refuse.
func TestUsageWriterDropsAndCountsOnOverflow(t *testing.T) {
	// A sink wedged inside insert holds the flusher, so the channel fills and
	// stays full — the only reliable way to reach the overflow branch.
	sink := &fakeSink{block: make(chan struct{})}
	defer close(sink.block)

	const bufSize = 4
	w, m := newTestWriter(t, usageTestConfig(bufSize, 1, time.Hour), sink)

	// Park the flusher inside insert before filling the buffer, so it cannot drain
	// a row out from under the count below.
	w.Write(sampleRow("wedge"))
	testutil.RequireEventually(t, 2*time.Second, 5*time.Millisecond,
		func() bool { return len(w.ch) == 0 },
		"the flusher should have taken the first row and be blocked in insert")

	// bufSize rows fill the channel; everything after that must be dropped.
	const overflow = 20
	for i := range bufSize + overflow {
		w.Write(sampleRow(fmt.Sprintf("r%d", i)))
	}

	if v := testutil.CounterValue(t, m.usageDropped); v != overflow {
		t.Errorf("usage_rows_dropped_total = %v, want %d "+
			"(buffer of %d, %d writes past it)", v, overflow, bufSize, overflow)
	}
}

// Write must never block, whatever the sink is doing. This is the property that
// lets it be called from a request handler at all — a Write that could block
// would put ClickHouse's availability on the request path.
func TestUsageWriterWriteNeverBlocks(t *testing.T) {
	sink := &fakeSink{block: make(chan struct{})}
	defer close(sink.block)

	w, _ := newTestWriter(t, usageTestConfig(1, 1, time.Hour), sink)

	// A tiny buffer and a wedged sink: after the first couple of writes every
	// subsequent one hits the full-buffer path. All must return promptly.
	done := make(chan struct{})
	go func() {
		defer close(done)
		for i := range 1000 {
			w.Write(sampleRow(fmt.Sprintf("r%d", i)))
		}
	}()

	select {
	case <-done:
	case <-time.After(5 * time.Second):
		// Fatal rather than a hang: a blocking Write would otherwise wedge the
		// whole suite with no indication of which property broke.
		t.Fatal("Write blocked — it must be fire-and-forget even with the sink stalled")
	}
}

// --- failure containment ---

// A failed insert must cost its batch and nothing else. In particular the writer
// has to keep accepting and flushing afterwards: a sink error that wedged the
// flusher would silently convert into dropped rows for the rest of the process's
// life.
func TestUsageWriterSurvivesInsertFailure(t *testing.T) {
	sink := &fakeSink{err: errors.New("clickhouse is unwell")}
	w, m := newTestWriter(t, usageTestConfig(100, 1, 20*time.Millisecond), sink)

	w.Write(sampleRow("doomed"))
	testutil.RequireEventually(t, 2*time.Second, 5*time.Millisecond,
		func() bool { return testutil.CounterValue(t, m.usageFlushErrors) == 1 },
		"a failed insert should increment usage_flush_errors_total")

	// The batch is discarded, not retried — retrying would grow the memory the
	// buffer exists to bound.
	if v := testutil.CounterValue(t, m.usageRows); v != 0 {
		t.Errorf("usage_rows_written_total = %v, want 0 — a failed batch is not written", v)
	}

	// Recovery: the sink starts working and the writer is still alive to use it.
	sink.mu.Lock()
	sink.err = nil
	sink.mu.Unlock()

	w.Write(sampleRow("survivor"))
	testutil.RequireEventually(t, 2*time.Second, 5*time.Millisecond,
		func() bool { return testutil.CounterValue(t, m.usageRows) == 1 },
		"the writer must keep flushing after an insert failure")
}

// --- shutdown ---

// Close FLUSHES rather than drops, for the reason main.go flushes spans: the last
// rows before a process goes down are the ones someone will ask about.
func TestUsageWriterCloseFlushesRemainder(t *testing.T) {
	sink := &fakeSink{}
	m := newMetricsWith(prometheus.NewRegistry())
	// A batch size and interval that can never fire on their own, so anything the
	// sink receives can only have come from the shutdown drain.
	w := newUsageWriterWithSink(usageTestConfig(100, 1000, time.Hour), sink, m,
		slog.New(slog.NewTextHandler(io.Discard, nil)))

	w.Write(sampleRow("a"))
	w.Write(sampleRow("b"))

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := w.Close(ctx); err != nil {
		t.Fatalf("Close: %v", err)
	}

	if got := sink.rowCount(); got != 2 {
		t.Errorf("rows after Close = %d, want 2 — buffered rows must be flushed, not dropped", got)
	}
}

// A row written after Close is dropped AND COUNTED. Without the closed check it
// would land in a channel nothing reads any more — lost silently, and missing
// from the counter that is supposed to account for every lost row.
func TestUsageWriterWriteAfterCloseIsCounted(t *testing.T) {
	sink := &fakeSink{}
	m := newMetricsWith(prometheus.NewRegistry())
	w := newUsageWriterWithSink(usageTestConfig(100, 1000, time.Hour), sink, m,
		slog.New(slog.NewTextHandler(io.Discard, nil)))

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := w.Close(ctx); err != nil {
		t.Fatalf("Close: %v", err)
	}

	w.Write(sampleRow("too late"))

	if v := testutil.CounterValue(t, m.usageDropped); v != 1 {
		t.Errorf("usage_rows_dropped_total = %v, want 1 — a post-Close row is a counted drop", v)
	}
	if got := sink.rowCount(); got != 0 {
		t.Errorf("rows = %d, want 0 — nothing should reach the sink after Close", got)
	}
}

// Close must be safe to call more than once. A shutdown path that panics on a
// double Close is worse than one that leaks, and main's defer ordering makes a
// second call easy to introduce.
func TestUsageWriterCloseIsIdempotent(t *testing.T) {
	w := newUsageWriterWithSink(usageTestConfig(10, 10, time.Hour), &fakeSink{},
		newMetricsWith(prometheus.NewRegistry()),
		slog.New(slog.NewTextHandler(io.Discard, nil)))

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := w.Close(ctx); err != nil {
		t.Fatalf("first Close: %v", err)
	}
	if err := w.Close(ctx); err != nil {
		t.Fatalf("second Close: %v", err)
	}
}

// --- nil writer ---

// A nil *UsageWriter is a working no-op, which is what lets a deployment without
// ClickHouse — and every other test in this package — run with no branch at any
// call site. Same convention as a nil *BreakerSharer.
func TestNilUsageWriterIsANoOp(t *testing.T) {
	var w *UsageWriter
	w.Write(sampleRow("into the void"))
	if err := w.Close(context.Background()); err != nil {
		t.Errorf("nil Close = %v, want nil", err)
	}
}

// --- request id ---

// The trace id is what joins a usage row to its span tree. Without a recording
// span there is none, and the UUID fallback is load-bearing rather than
// decorative: tracing is off by default, and an all-zero trace id stored on every
// untraced request would silently collapse GROUP BY request_id.
func TestRequestIDPrefersTraceID(t *testing.T) {
	traceID, err := trace.TraceIDFromHex("4bf92f3577b34da6a3ce929d0e0e4736")
	if err != nil {
		t.Fatalf("bad fixture trace id: %v", err)
	}
	spanID, err := trace.SpanIDFromHex("00f067aa0ba902b7")
	if err != nil {
		t.Fatalf("bad fixture span id: %v", err)
	}
	ctx := trace.ContextWithSpanContext(context.Background(), trace.NewSpanContext(
		trace.SpanContextConfig{TraceID: traceID, SpanID: spanID, TraceFlags: trace.FlagsSampled},
	))

	if got := requestID(ctx); got != traceID.String() {
		t.Errorf("requestID = %q, want the trace id %q", got, traceID.String())
	}
}

func TestRequestIDFallsBackToUUID(t *testing.T) {
	first := requestID(context.Background())
	if first == "" {
		t.Fatal("requestID returned empty for an untraced context")
	}
	// An all-zero trace id is the specific wrong answer: it is what a
	// non-recording span carries, and storing it would give every untraced request
	// the same id.
	if first == "00000000000000000000000000000000" {
		t.Error("requestID returned the zero trace id; the UUID fallback did not fire")
	}
	if second := requestID(context.Background()); second == first {
		t.Error("requestID returned the same id twice; ids must be unique per request")
	}
}

// --- the production constructor ---

// An empty address is the whole off switch: nothing is constructed, no goroutine
// runs and no connection is opened. main relies on the nil to skip the usage log
// without a branch of its own.
func TestNewUsageWriterDisabledWithoutAddress(t *testing.T) {
	cfg := usageTestConfig(10, 10, time.Hour)
	cfg.ClickHouseAddr = ""

	w, err := newUsageWriter(context.Background(), cfg,
		newMetricsWith(prometheus.NewRegistry()),
		slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatalf("newUsageWriter with no address = %v, want nil error", err)
	}
	if w != nil {
		t.Error("newUsageWriter with no address returned a writer; empty must disable it entirely")
	}
}

// An unreachable ClickHouse must yield a WORKING writer, not an error. The
// gateway has no depends_on for it, so losing a startup race with a still-booting
// container must not permanently disable the usage log — and a usage log is never
// a reason to refuse to serve requests.
func TestNewUsageWriterSurvivesUnreachableClickHouse(t *testing.T) {
	cfg := usageTestConfig(10, 10, time.Hour)
	cfg.ClickHouseAddr = "127.0.0.1:1" // reserved; nothing listens
	cfg.ClickHouseDatabase = "default"
	cfg.ClickHouseUser = "default"

	w, err := newUsageWriter(context.Background(), cfg,
		newMetricsWith(prometheus.NewRegistry()),
		slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatalf("newUsageWriter against a dead address = %v, want a usable writer", err)
	}
	if w == nil {
		t.Fatal("newUsageWriter returned nil for an unreachable server; it must stay enabled and retry")
	}
	// And it must still absorb writes without blocking the caller.
	w.Write(sampleRow("into the dark"))

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = w.Close(ctx)
}

// --- integration: a batch round-trips through a real ClickHouse ---

// The production constructor against a real server: it connects, creates the
// table on a database that does not have it yet, and returns a working writer.
//
// This covers the "stale volume" path — a developer whose ClickHouse predates
// schema.sql never ran the init script, so ensureSchema at startup is the only
// thing that gives them the table.
func TestNewUsageWriterEnsuresSchemaOnStartup(t *testing.T) {
	// Gate on the same reachability check the other integration tests use, and
	// drop the table so ensureSchema has something to actually do.
	conn := testutil.RequireClickHouse(t)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := conn.Exec(ctx, "DROP TABLE IF EXISTS "+usageTable); err != nil {
		t.Fatalf("drop table: %v", err)
	}

	cfg := usageTestConfig(10, 10, time.Hour)
	cfg.ClickHouseAddr = testutil.ClickHouseAddr()
	cfg.ClickHouseDatabase = testutil.TestClickHouseDatabase
	cfg.ClickHouseUser = "default"

	w, err := newUsageWriter(ctx, cfg, newMetricsWith(prometheus.NewRegistry()),
		slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatalf("newUsageWriter: %v", err)
	}
	if w == nil {
		t.Fatal("newUsageWriter returned nil against a reachable server")
	}
	defer func() { _ = w.Close(ctx) }()

	var n uint64
	if err = conn.QueryRow(ctx,
		"SELECT count() FROM system.tables WHERE database = ? AND name = ?",
		testutil.TestClickHouseDatabase, usageTable).Scan(&n); err != nil {
		t.Fatalf("check table: %v", err)
	}
	if n != 1 {
		t.Errorf("table %q exists = %d, want 1 — startup must create it on a volume "+
			"that never ran the init script", usageTable, n)
	}
}

// This is the acceptance criterion the fake sink cannot cover: that a row
// survives the driver's columnar encoding with every column and type intact.
//
// SKIPS when ClickHouse is absent, so `make test` stays green on a laptop — which
// means CI is the environment that has to supply one, and llmguard-ci.yml does.
func TestUsageWriterInsertsAndReadsBackABatch(t *testing.T) {
	conn := testutil.RequireClickHouse(t)

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	sink := &clickhouseSink{conn: conn}
	if err := sink.ensureSchema(ctx); err != nil {
		t.Fatalf("ensureSchema: %v", err)
	}

	// Values chosen so a silently wrong column ORDER cannot pass: every numeric
	// field is distinct, and the two that the wire format could most plausibly
	// mangle — an exact Decimal and a millisecond timestamp — are checked exactly.
	want := []UsageRow{
		{
			TS:               time.Date(2026, 8, 22, 10, 30, 15, 123000000, time.UTC),
			RequestID:        "4bf92f3577b34da6a3ce929d0e0e4736",
			Model:            "gemini-2.5-flash",
			Provider:         "vertex",
			PromptTokens:     101,
			CompletionTokens: 202,
			CostUSD:          decimal.RequireFromString("0.00007500"),
			LatencyMS:        3456,
			UpstreamStatus:   200,
			Retries:          2,
			DedupHit:         true,
			APIKeyHint:       "abcdef",
		},
		{
			TS:               time.Date(2026, 8, 22, 10, 30, 16, 456000000, time.UTC),
			RequestID:        "9f1e2d3c4b5a69788796a5b4c3d2e1f0",
			Model:            "gemini-2.5-pro",
			Provider:         "openai",
			PromptTokens:     303,
			CompletionTokens: 404,
			CostUSD:          decimal.RequireFromString("1.23456789"),
			LatencyMS:        7890,
			// 0 is the "never reached upstream" encoding — worth round-tripping,
			// since a Nullable column would have stored it as NULL instead.
			UpstreamStatus: 0,
			Retries:        0,
			DedupHit:       false,
			APIKeyHint:     "anon",
		},
	}

	if err := sink.insert(ctx, want); err != nil {
		t.Fatalf("insert: %v", err)
	}

	rows, err := conn.Query(ctx, `
		SELECT ts, request_id, model, provider, prompt_tokens, completion_tokens,
		       cost_usd, latency_ms, upstream_status, retries, dedup_hit, api_key_hint
		FROM `+usageTable+`
		ORDER BY ts`)
	if err != nil {
		t.Fatalf("select: %v", err)
	}
	defer rows.Close()

	var got []UsageRow
	for rows.Next() {
		var r UsageRow
		if scanErr := rows.Scan(
			&r.TS, &r.RequestID, &r.Model, &r.Provider,
			&r.PromptTokens, &r.CompletionTokens, &r.CostUSD, &r.LatencyMS,
			&r.UpstreamStatus, &r.Retries, &r.DedupHit, &r.APIKeyHint,
		); scanErr != nil {
			t.Fatalf("scan: %v", scanErr)
		}
		got = append(got, r)
	}
	if rowsErr := rows.Err(); rowsErr != nil {
		t.Fatalf("rows: %v", rowsErr)
	}

	if len(got) != len(want) {
		t.Fatalf("read back %d rows, want %d", len(got), len(want))
	}
	for i := range want {
		w, g := want[i], got[i]
		// Compared field by field rather than with reflect.DeepEqual, because
		// time.Time and decimal.Decimal both carry unexported state that differs
		// after a round trip even when the VALUE is identical.
		if !g.TS.Equal(w.TS) {
			t.Errorf("row %d ts = %v, want %v", i, g.TS.UTC(), w.TS)
		}
		if !g.CostUSD.Equal(w.CostUSD) {
			t.Errorf("row %d cost_usd = %s, want %s (exact decimal must survive)",
				i, g.CostUSD, w.CostUSD)
		}
		if g.RequestID != w.RequestID || g.Model != w.Model || g.Provider != w.Provider ||
			g.APIKeyHint != w.APIKeyHint {
			t.Errorf("row %d string columns = %+v, want %+v", i, g, w)
		}
		if g.PromptTokens != w.PromptTokens || g.CompletionTokens != w.CompletionTokens ||
			g.LatencyMS != w.LatencyMS || g.UpstreamStatus != w.UpstreamStatus ||
			g.Retries != w.Retries {
			t.Errorf("row %d numeric columns = %+v, want %+v", i, g, w)
		}
		if g.DedupHit != w.DedupHit {
			t.Errorf("row %d dedup_hit = %v, want %v", i, g.DedupHit, w.DedupHit)
		}
	}
}

// The writer end to end against a real ClickHouse: rows go in through the public
// Write, and Close is what gets them written.
func TestUsageWriterEndToEndAgainstClickHouse(t *testing.T) {
	conn := testutil.RequireClickHouse(t)

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	sink := &clickhouseSink{conn: conn}
	if err := sink.ensureSchema(ctx); err != nil {
		t.Fatalf("ensureSchema: %v", err)
	}

	m := newMetricsWith(prometheus.NewRegistry())
	w := newUsageWriterWithSink(usageTestConfig(100, 1000, time.Hour), sink, m,
		slog.New(slog.NewTextHandler(io.Discard, nil)))

	const rows = 7
	for i := range rows {
		w.Write(sampleRow(fmt.Sprintf("e2e-%d", i)))
	}
	if err := w.Close(ctx); err != nil {
		t.Fatalf("Close: %v", err)
	}

	// conn is closed by Close (the sink owns it), so count through a fresh one.
	verify := testutil.RequireClickHouse(t)
	var n uint64
	if err := verify.QueryRow(ctx,
		"SELECT count() FROM "+usageTable+" WHERE request_id LIKE 'e2e-%'").Scan(&n); err != nil {
		t.Fatalf("count: %v", err)
	}
	if n != rows {
		t.Errorf("rows in ClickHouse = %d, want %d", n, rows)
	}
	if v := testutil.CounterValue(t, m.usageRows); v != rows {
		t.Errorf("usage_rows_written_total = %v, want %d", v, rows)
	}
}
