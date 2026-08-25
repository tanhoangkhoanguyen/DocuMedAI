package gateway

// The usage log: a durable, per-request record in ClickHouse.
//
// Why a third observability surface when there are already metrics and traces:
// Prometheus is pre-aggregated and forgets individuals, and traces are sampled
// and expire in the collector's storage. Neither can answer "what did this API
// key spend last Tuesday", which is a question about a specific row that has to
// still be there weeks later. That is a database, not a counter.
//
// THE LOAD-BEARING CONSTRAINT: a billing sink must never become a new way to
// fail a request. LLMGuard's whole claim is reliable LLM calls, so a component
// added for accounting must not be able to add latency, hold a request, or grow
// without bound. Everything below follows from that one requirement:
//
//   - Write() is fire-and-forget. It cannot block and cannot fail, so it returns
//     nothing. There is no error a request handler could usefully act on, and
//     giving it one would only invite an `if err != nil` on the hot path.
//   - The hand-off is a BOUNDED channel with a non-blocking send. The buffer is a
//     hard ceiling on memory, and past it rows are dropped and counted — the same
//     shape as admission.go's semaphore, but the overflow costs a billing row
//     instead of a request.
//   - A failed insert DISCARDS its batch rather than retrying. Retrying in
//     process would grow exactly the memory the buffer exists to bound, turning a
//     ClickHouse outage into a gateway outage. Durable delivery across a sink
//     outage is the deferred Kafka row in ROADMAP.md.
//
// A nil *UsageWriter is fully usable and does nothing, so a deployment without
// ClickHouse needs no branch at any call site — the same convention BreakerSharer
// uses for a single-replica deployment.

import (
	"context"
	_ "embed"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"
	"github.com/google/uuid"
	"github.com/shopspring/decimal"
	"go.opentelemetry.io/otel/trace"
)

// usageSchema is the table definition, shared byte-for-byte with the copy
// docker-compose mounts into ClickHouse's init directory.
//
// Embedded rather than duplicated as a Go string literal because the two are
// applied by different paths — the image's entrypoint on a fresh volume, this
// process on every start — and a schema that can drift between them is a class
// of bug nobody finds until a column is missing in production.
//
//go:embed schema.sql
var usageSchema string

// usageTable is the table name, matching schema.sql. Used to build the INSERT.
//
// Prefixed rather than living in a dedicated `llmguard` database because the
// ClickHouse image runs init scripts against `default` regardless of
// CLICKHOUSE_DB — see the comment at the top of schema.sql.
const usageTable = "llmguard_usage"

// flushTimeout bounds one insert.
//
// A package constant rather than an eighth config knob: it is a backstop against
// a hung connection, not something an operator tunes. It is deliberately much
// larger than UsageFlushInterval — flushes are serial on one goroutine, so a slow
// insert simply lets rows accumulate in the bounded channel, which is the
// designed behavior rather than a problem to solve with a tighter deadline.
const flushTimeout = 30 * time.Second

// UsageRow is one completed request.
//
// Field order and types mirror schema.sql exactly, because appendRow below binds
// them positionally — a reordering here without one there is a silent column
// mismatch rather than a compile error.
type UsageRow struct {
	TS        time.Time
	RequestID string
	Model     string
	Provider  string

	PromptTokens     uint32
	CompletionTokens uint32

	// CostUSD is exact decimal, not a float: this column is summed per key and
	// per model, and float error accumulates across millions of rows.
	CostUSD decimal.Decimal

	LatencyMS uint32

	// UpstreamStatus is 0 when the request never reached upstream — shed, rate
	// limited, or failed fast on an open breaker.
	UpstreamStatus uint16

	// Retries counts attempts after the first, matching llmguard_retries_total
	// rather than doWithRetry's 0-based attempt number.
	Retries uint8

	// DedupHit means the flight had more than one caller. True for the flight
	// LEADER too — it means coalesced, not "reused someone else's response".
	DedupHit bool

	APIKeyHint string
}

// usageSink is where a flushed batch goes.
//
// An interface for one reason: it lets the buffer, the flush triggers, the
// overflow drop and the shutdown drain all be tested without a ClickHouse, which
// is most of this file's actual logic. The production implementation is
// clickhouseSink below; tests substitute a fake, the same seam harness_test.go's
// mockProvider provides for the request pipeline.
type usageSink interface {
	insert(ctx context.Context, rows []UsageRow) error
	close() error
}

// UsageWriter accepts usage rows and inserts them into ClickHouse in batches.
//
// A nil *UsageWriter is a working no-op — every method tolerates a nil receiver.
type UsageWriter struct {
	sink    usageSink
	metrics *Metrics
	log     *slog.Logger

	batchSize     int
	flushInterval time.Duration

	// ch is the hand-off from request goroutines to the single flusher. Its
	// capacity IS the memory ceiling: a stalled sink can cost at most this many
	// rows before Write starts dropping.
	ch chan UsageRow

	// drops accumulates overflow between ticks so the flusher can emit ONE log
	// line per interval instead of one per row. The scenario that produces drops
	// is precisely the scenario where per-row logging is a log flood.
	drops atomic.Int64

	// closed makes a post-Close Write an honest, counted drop rather than a row
	// that lands in a channel nothing is reading any more.
	closed atomic.Bool

	stopOnce sync.Once
	done     chan struct{}
	finished chan struct{}
}

// newUsageWriter connects to ClickHouse, ensures the table exists and starts the
// flusher.
//
// An empty cfg.ClickHouseAddr returns (nil, nil): the usage log is off and a nil
// writer is a working no-op, so main needs no conditional around the result.
//
// A ClickHouse that is unreachable at startup is a WARNING, not an error. The
// gateway deliberately has no depends_on for it, so losing the race with a
// still-starting container must not permanently disable the usage log — the
// driver's pool reconnects on its own and flushes begin succeeding once the
// server appears. Only options the driver rejects outright are fatal, since those
// cannot fix themselves.
func newUsageWriter(
	ctx context.Context, cfg Config, m *Metrics, log *slog.Logger,
) (*UsageWriter, error) {
	if cfg.ClickHouseAddr == "" {
		return nil, nil
	}

	conn, err := clickhouse.Open(&clickhouse.Options{
		Addr: []string{cfg.ClickHouseAddr},
		Auth: clickhouse.Auth{
			Database: cfg.ClickHouseDatabase,
			Username: cfg.ClickHouseUser,
			Password: cfg.ClickHousePassword,
		},
		// LZ4 because the payload is highly repetitive (a handful of distinct
		// model/provider/key values across a whole batch) and the gateway has CPU
		// to spare while its requests wait on an LLM.
		Compression:     &clickhouse.Compression{Method: clickhouse.CompressionLZ4},
		DialTimeout:     5 * time.Second,
		MaxOpenConns:    4,
		MaxIdleConns:    2,
		ConnMaxLifetime: time.Hour,
	})
	if err != nil {
		return nil, err
	}

	sink := &clickhouseSink{conn: conn}
	if perr := conn.Ping(ctx); perr != nil {
		log.Warn("clickhouse unreachable at startup; usage rows will be dropped until it appears",
			"addr", cfg.ClickHouseAddr, "err", perr.Error())
	} else if serr := sink.ensureSchema(ctx); serr != nil {
		// Also non-fatal, and for the same reason: the compose init script may
		// already have created the table, and a transient DDL failure must not
		// take down a gateway that serves requests perfectly well without it.
		log.Warn("clickhouse schema setup failed", "err", serr.Error())
	}

	return newUsageWriterWithSink(cfg, sink, m, log), nil
}

// newUsageWriterWithSink is the constructor the tests use, with the ClickHouse
// connection already substituted.
func newUsageWriterWithSink(
	cfg Config, sink usageSink, m *Metrics, log *slog.Logger,
) *UsageWriter {
	w := &UsageWriter{
		sink:          sink,
		metrics:       m,
		log:           log,
		batchSize:     cfg.UsageBatchSize,
		flushInterval: cfg.UsageFlushInterval,
		ch:            make(chan UsageRow, cfg.UsageBufferSize),
		done:          make(chan struct{}),
		finished:      make(chan struct{}),
	}
	go w.run()
	return w
}

// Write hands off one row.
//
// It NEVER blocks and NEVER returns an error — see the package comment. A full
// buffer drops the row and counts it, which is the deliberate trade: losing a
// billing row is recoverable, adding latency to every request is not.
func (w *UsageWriter) Write(row UsageRow) {
	if w == nil {
		return
	}
	// After Close the flusher has exited, so a send that "succeeds" would put a
	// row into a channel nothing will ever read — lost silently, and invisible in
	// the drop counter that is supposed to account for every lost row.
	if w.closed.Load() {
		w.metrics.usageDropped.Inc()
		return
	}

	select {
	case w.ch <- row:
	default:
		// The bounded buffer doing its job. Counted here, logged once per tick by
		// the flusher.
		w.metrics.usageDropped.Inc()
		w.drops.Add(1)
	}
}

// Close stops the flusher, drains what is buffered and writes it.
//
// FLUSH rather than drop, for the reason main.go flushes spans at shutdown: the
// last rows before a process goes down are the most interesting ones, not the
// least — a deploy or a crash is exactly when someone asks what the final
// requests did.
//
// BOUNDED by the caller's context, for the reason stated on TraceShutdownGrace: a
// sink that has itself gone away must not be able to spend a shutdown window that
// something else still needs. On timeout this returns ctx.Err() and leaves the
// flusher to exit on its own; the process is going down regardless.
func (w *UsageWriter) Close(ctx context.Context) error {
	if w == nil {
		return nil
	}
	// Once, because a double Close would panic on the second close(w.done) — and
	// a shutdown path that panics is worse than one that leaks.
	w.stopOnce.Do(func() {
		w.closed.Store(true)
		close(w.done)
	})

	select {
	case <-w.finished:
		return w.sink.close()
	case <-ctx.Done():
		return ctx.Err()
	}
}

// run is the single flusher goroutine. It exclusively owns `batch`, which is why
// there is no mutex anywhere in this file.
func (w *UsageWriter) run() {
	defer close(w.finished)

	ticker := time.NewTicker(w.flushInterval)
	defer ticker.Stop()

	batch := make([]UsageRow, 0, w.batchSize)

	for {
		select {
		case row := <-w.ch:
			batch = append(batch, row)
			// Flush-by-SIZE: caps how large one insert gets, and with it the memory
			// one batch holds and the blast radius of one failed insert.
			if len(batch) >= w.batchSize {
				batch = w.flush(batch)
			}

		case <-ticker.C:
			// Flush-by-INTERVAL bounds a different quantity: how long a row may sit
			// unwritten. At low traffic the size trigger never fires, so without
			// this a handful of rows would stay in memory indefinitely.
			//
			// The ticker is deliberately NOT reset after a size-triggered flush. The
			// worst case is one near-empty flush immediately after a full one, which
			// is cheap, and it avoids the reset races a time.Timer invites. Maximum
			// staleness is ~flushInterval either way.
			if len(batch) > 0 {
				batch = w.flush(batch)
			}
			w.reportDrops()
			w.metrics.usageBufferDepth.Set(float64(len(w.ch) + len(batch)))

		case <-w.done:
			// Drain whatever is already queued, then write it. The receive is
			// non-blocking and the channel has a fixed capacity, so this terminates
			// even if a producer is still calling Write — and those late rows are
			// dropped and counted by Write's closed check rather than looping here
			// forever.
			for {
				select {
				case row := <-w.ch:
					batch = append(batch, row)
					if len(batch) >= w.batchSize {
						batch = w.flush(batch)
					}
					continue
				default:
				}
				break
			}
			if len(batch) > 0 {
				w.flush(batch)
			}
			w.reportDrops()
			return
		}
	}
}

// flush writes one batch and returns the slice to reuse.
//
// The batch is DISCARDED whether the insert succeeded or failed. Retrying would
// grow the memory the bounded buffer exists to cap, which would turn a ClickHouse
// outage into a gateway outage — the exact failure this whole design refuses.
// llmguard_usage_flush_errors_total is what makes the loss visible.
func (w *UsageWriter) flush(batch []UsageRow) []UsageRow {
	ctx, cancel := context.WithTimeout(context.Background(), flushTimeout)
	defer cancel()

	if err := w.sink.insert(ctx, batch); err != nil {
		w.metrics.usageFlushErrors.Inc()
		w.log.Warn("usage flush failed; batch discarded",
			"rows", len(batch), "err", err.Error())
	} else {
		w.metrics.usageRows.Add(float64(len(batch)))
	}
	return batch[:0]
}

// reportDrops emits at most one line per tick for however many rows overflowed.
func (w *UsageWriter) reportDrops() {
	if n := w.drops.Swap(0); n > 0 {
		w.log.Warn("usage buffer full; rows dropped", "rows", n)
	}
}

// --- ClickHouse sink ---

type clickhouseSink struct {
	conn driver.Conn
}

// ensureSchema applies schema.sql.
//
// Not redundant with the compose init script: the ClickHouse image runs
// /docker-entrypoint-initdb.d only when the data directory is EMPTY, so a
// developer whose volume predates the table would never get it. CREATE TABLE IF
// NOT EXISTS makes running it on every start free.
func (s *clickhouseSink) ensureSchema(ctx context.Context) error {
	return s.conn.Exec(ctx, usageSchema)
}

// insert writes one batch as a single columnar block.
//
// PrepareBatch/Append/Send is the driver's native path: the whole batch travels
// as one block, which is what ClickHouse wants. The database/sql surface would
// issue a statement per row and create a part per insert.
func (s *clickhouseSink) insert(ctx context.Context, rows []UsageRow) error {
	batch, err := s.conn.PrepareBatch(ctx, "INSERT INTO "+usageTable)
	if err != nil {
		return err
	}
	for _, r := range rows {
		if err = batch.Append(
			r.TS,
			r.RequestID,
			r.Model,
			r.Provider,
			r.PromptTokens,
			r.CompletionTokens,
			r.CostUSD,
			r.LatencyMS,
			r.UpstreamStatus,
			r.Retries,
			r.DedupHit,
			r.APIKeyHint,
		); err != nil {
			// Abort releases the batch's server-side resources. Without it a
			// half-built batch leaks until the connection is recycled.
			_ = batch.Abort()
			return err
		}
	}
	return batch.Send()
}

func (s *clickhouseSink) close() error { return s.conn.Close() }

// --- request id ---

// requestID derives the id stored on a usage row.
//
// The OpenTelemetry trace id when the request is being traced, because that is
// what JOINS this row to its span tree: the row says what the request cost, the
// trace says why it was slow, and without a shared id neither can lead to the
// other.
//
// The UUID fallback is not a nicety — tracing is OFF by default (see
// Config.TraceEndpoint), and a non-recording span carries an all-zero trace id.
// Storing that would give every untraced request the same "id", which is worse
// than useless: it would silently collapse GROUP BY request_id.
func requestID(ctx context.Context) string {
	if sc := trace.SpanContextFromContext(ctx); sc.HasTraceID() {
		return sc.TraceID().String()
	}
	return uuid.NewString()
}
