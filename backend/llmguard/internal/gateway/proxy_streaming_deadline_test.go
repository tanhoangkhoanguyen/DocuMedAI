package gateway

// The streaming path's deadlines: what bounds a stream, and what must NOT.
//
// Like admission_test.go, and unlike the rest of this suite, these tests state
// INTENDED behavior rather than pinning prior conduct — the behavior is new, so
// there is nothing to preserve. What they encode is the contract that made the
// change necessary:
//
//   - A stream is bounded by INACTIVITY, never by total duration. A healthy
//     generation runs as long as it likes; a stalled one is cut.
//   - The thing that must come back is the ADMISSION SLOT, not merely the
//     request. A handler that returns while its goroutine still holds a slot
//     looks fine to every status assertion and still ratchets the gateway down
//     to zero capacity. Every test here asserts on llmguard_in_flight.
//
// Harness and thresholds live in harness_test.go.

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"

	"documedai/llmguard/internal/testutil"
	"documedai/llmguard/mockupstream"
)

// streamingConfig is realDefaults with a ceiling of 1, so in_flight is a direct
// readout of "is a stream still holding its slot" rather than a number to
// compare against a large limit.
func streamingConfig() Config {
	cfg := realDefaults()
	cfg.MaxInFlight = 1
	return cfg
}

// requireSlotReleased waits for the admission gauge to return to zero.
//
// The load-bearing assertion of this file. A stream that ends still has to
// unwind — close the upstream body, run the deferred release — and that happens
// after the handler's last write, so it is observed by polling rather than by
// reading the recorder.
func requireSlotReleased(t *testing.T, h *harness, within time.Duration, msg string) {
	t.Helper()
	testutil.RequireEventually(t, within, 5*time.Millisecond, func() bool {
		return testutil.CounterValue(t, h.metrics.inFlight) == 0
	}, msg)
}

// A healthy stream that runs longer than UpstreamTimeout must survive it.
//
// This is the bug the client split fixes. http.Client.Timeout is an absolute
// deadline covering the body read, so while one client served both paths, a
// stream was cut at UPSTREAM_TIMEOUT no matter how healthy it was — the frames
// already delivered were kept, an error frame was appended, and the caller saw a
// truncation reported as an upstream failure. Nothing detected it, because from
// the outside a truncated stream and a short completion look the same.
//
// 10 frames × 40ms ≈ 400ms of streaming against a 150ms UpstreamTimeout: the old
// single-client build cannot reach the end of this stream.
func TestStreamOutlivesUpstreamTimeout(t *testing.T) {
	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 10
	mcfg.ChunkDelay = 40 * time.Millisecond

	cfg := streamingConfig()
	cfg.UpstreamTimeout = 150 * time.Millisecond // buffered path only
	h := newHarness(t, cfg, mcfg, nil)

	start := time.Now()
	rec := h.do(t, chatBody("gemini-2.5-flash", "stream this", true), nil)
	elapsed := time.Since(start)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if elapsed < cfg.UpstreamTimeout {
		t.Fatalf("stream finished in %s, before the %s buffered timeout — the mock did not "+
			"actually stream long enough to exercise this", elapsed, cfg.UpstreamTimeout)
	}

	body := rec.Body.String()
	if !strings.HasSuffix(strings.TrimSpace(body), "data: [DONE]") {
		t.Errorf("stream must end with data: [DONE]; tail = %q", body[max(0, len(body)-40):])
	}
	// A truncation would appear as an in-band error frame appended to whatever was
	// delivered. Its absence is what says the stream completed rather than survived.
	if strings.Contains(body, "upstream stream failed") {
		t.Error("a healthy stream was cut short: UPSTREAM_TIMEOUT must not bound the streaming path")
	}

	requireSlotReleased(t, h, time.Second, "a completed stream must return its slot")
}

// An upstream that delivers frames and then goes silent is cut at the
// inter-frame deadline, and the slot comes back.
//
// The leak this closes: nothing measured the gap between two frames. A
// slow-loris upstream is "active" to any total-duration bound, so the slot was
// held at the upstream's pace rather than at any limit of the gateway's. Only
// the 30m backstop would have ended it — long enough that a real outage
// exhausts every slot first.
//
// Frames delivered before the stall must survive, for the same reason the
// post-header failure path keeps them: the caller has a real partial answer, and
// discarding it to send a clean error is the worse outcome.
func TestStreamAbortsOnUpstreamIdle(t *testing.T) {
	const delivered = 3

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 20
	mcfg.StallAfter = delivered

	cfg := streamingConfig()
	cfg.StreamIdleTimeout = 150 * time.Millisecond
	h := newHarness(t, cfg, mcfg, nil)

	start := time.Now()
	rec := h.do(t, chatBody("gemini-2.5-flash", "stream this", true), nil)
	elapsed := time.Since(start)

	// The header went out with the first frame, so the status is locked at 200 and
	// the failure can only be reported in band.
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 — frames were already flushed", rec.Code)
	}
	// Bounded by inactivity, not by the 30m backstop, which would hang the suite.
	if elapsed > 5*time.Second {
		t.Fatalf("stream took %s to abort; the inter-frame deadline did not fire", elapsed)
	}

	body := rec.Body.String()
	if got := countDataFrames(body); got < delivered {
		t.Errorf("delivered %d frames, want at least %d — frames sent before the stall must survive",
			got, delivered)
	}
	// The decisive assertion. Without the abortReason check in serveStreaming a
	// cancelled read can surface as a clean EOF, and the stream would end with a
	// bare [DONE] — a truncation reported to the caller as a complete answer.
	if !strings.Contains(body, "upstream stream failed") {
		t.Error("no in-band error frame; a stalled stream must be reported, not silently " +
			"passed off as a complete one")
	}
	if !strings.HasSuffix(strings.TrimSpace(body), "data: [DONE]") {
		t.Error("an aborted stream must still terminate so a client read loop exits")
	}

	if got := testutil.LabeledCounterValue(t, h.metrics.streamAborts,
		modelLabels("gemini-2.5-flash", abortUpstreamIdle)...); got != 1 {
		t.Errorf("stream_aborts_total{reason=upstream_idle} = %v, want 1", got)
	}
	requireSlotReleased(t, h, 2*time.Second, "an aborted stream must return its slot")
}

// The inter-frame deadline is RESET per frame, so a stream slower than the
// deadline in total never trips it.
//
// Without the reset the knob would be an absolute timeout under another name,
// and would cut exactly the long healthy generations the previous commit made
// possible. 8 frames × 60ms ≈ 480ms total against a 200ms deadline: every gap is
// comfortably under, the total is well over.
func TestStreamIdleDeadlineResetsPerFrame(t *testing.T) {
	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 8
	mcfg.ChunkDelay = 60 * time.Millisecond

	cfg := streamingConfig()
	cfg.StreamIdleTimeout = 200 * time.Millisecond
	h := newHarness(t, cfg, mcfg, nil)

	start := time.Now()
	rec := h.do(t, chatBody("gemini-2.5-flash", "stream this", true), nil)
	elapsed := time.Since(start)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if elapsed < cfg.StreamIdleTimeout {
		t.Fatalf("stream finished in %s, under the %s deadline — the gaps were too short to "+
			"prove the deadline resets", elapsed, cfg.StreamIdleTimeout)
	}
	if strings.Contains(rec.Body.String(), "upstream stream failed") {
		t.Error("a stream whose TOTAL time exceeds the deadline was aborted — " +
			"StreamIdleTimeout must measure the gap BETWEEN frames, not the whole stream")
	}
	if got := testutil.LabeledCounterValue(t, h.metrics.streamAborts,
		modelLabels("gemini-2.5-flash", abortUpstreamIdle)...); got != 0 {
		t.Errorf("stream_aborts_total = %v, want 0 — nothing stalled", got)
	}
	requireSlotReleased(t, h, time.Second, "a completed stream must return its slot")
}

// StreamIdleTimeout = 0 disables the watchdog, matching how MaxInFlight treats 0:
// an operator restoring the older unbounded behavior should get it.
//
// Asserted against a stalling upstream, so a watchdog that fired anyway would
// show up as an abort rather than passing silently.
func TestStreamIdleDisabledNeverAborts(t *testing.T) {
	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 20
	mcfg.StallAfter = 2

	cfg := streamingConfig()
	cfg.StreamIdleTimeout = 0
	// The backstop is what ends this request instead. Shortened from 30m so the
	// test terminates; the point is that it is the ABSOLUTE bound doing it.
	cfg.StreamAbsoluteMax = 400 * time.Millisecond
	h := newHarness(t, cfg, mcfg, nil)

	h.do(t, chatBody("gemini-2.5-flash", "stream this", true), nil)

	if got := testutil.LabeledCounterValue(t, h.metrics.streamAborts,
		modelLabels("gemini-2.5-flash", abortUpstreamIdle)...); got != 0 {
		t.Errorf("stream_aborts_total = %v, want 0 — the watchdog must be off at 0", got)
	}
	// The backstop is what ended it, and it must say so. StreamAbsoluteMax is
	// enforced by http.Client beneath us and cannot announce itself, so without
	// the inference in streamAbortReason it lands in no counter at all — invisible
	// exactly when it matters, since it only fires once the inactivity bounds have
	// failed to.
	if got := testutil.LabeledCounterValue(t, h.metrics.streamAborts,
		modelLabels("gemini-2.5-flash", abortAbsoluteMax)...); got != 1 {
		t.Errorf("stream_aborts_total{reason=absolute_max} = %v, want 1 — a backstop cut must "+
			"not be reported as an ordinary upstream error", got)
	}
	requireSlotReleased(t, h, 2*time.Second,
		"the absolute backstop must still return the slot with the watchdog disabled")
}

// A pre-header deadline is NOT a backstop cut.
//
// Before the first frame the same error means connect/TLS/first-byte was slow —
// an upstream problem. Counting it as absolute_max would fill the counter that
// is supposed to mean "the streaming deadlines are broken" with ordinary
// upstream slowness, which is how a real backstop firing gets lost in noise.
func TestSlowUpstreamBeforeHeaderIsNotABackstopAbort(t *testing.T) {
	mcfg := mockupstream.DefaultConfig()
	// Delay lands before any byte is written, so the deadline trips pre-header.
	mcfg.Latency = time.Second

	cfg := streamingConfig()
	cfg.StreamAbsoluteMax = 150 * time.Millisecond
	h := newHarness(t, cfg, mcfg, nil)

	rec := h.do(t, chatBody("gemini-2.5-flash", "stream this", true), nil)

	if rec.Code == http.StatusOK {
		t.Fatalf("status = 200; the stream should have failed before any frame")
	}
	if got := testutil.LabeledCounterValue(t, h.metrics.streamAborts,
		modelLabels("gemini-2.5-flash", abortAbsoluteMax)...); got != 0 {
		t.Errorf("stream_aborts_total{reason=absolute_max} = %v, want 0 — a pre-header timeout "+
			"is upstream slowness, not a stream that overran", got)
	}
	requireSlotReleased(t, h, time.Second, "a pre-header failure must return its slot")
}

// The buffered path keeps its absolute timeout. The split gave streaming its own
// client; it must not have loosened the other one, whose ceiling is correct
// precisely because a buffered body arrives in one piece.
func TestBufferedStillBoundByUpstreamTimeout(t *testing.T) {
	mcfg := mockupstream.DefaultConfig()
	mcfg.Latency = 600 * time.Millisecond

	cfg := streamingConfig()
	cfg.UpstreamTimeout = 100 * time.Millisecond
	// A timeout is a retryable failure, so without this the test pays the full
	// backoff schedule; how long a retry waits is irrelevant to the bound itself.
	cfg.RetryBaseDly = time.Millisecond
	cfg.RetryMaxDly = 2 * time.Millisecond
	h := newHarness(t, cfg, mcfg, nil)

	start := time.Now()
	rec := h.do(t, chatBody("gemini-2.5-flash", "buffered", false), nil)
	elapsed := time.Since(start)

	if rec.Code == http.StatusOK {
		t.Fatalf("status = 200 after %s; UPSTREAM_TIMEOUT must still bound the buffered path", elapsed)
	}
	requireSlotReleased(t, h, time.Second, "a timed-out buffered request must return its slot")
}

// A client that opens a stream and never reads must not pin its slot.
//
// The sharpest form of the leak, and the reason this test dials a real socket
// instead of using the recorder harness: Flush() blocks once the kernel send
// buffer fills, r.Context() does not fire because the client is silent rather
// than gone, and the inter-frame watchdog does not fire because the upstream is
// healthy. httptest.ResponseRecorder is an in-memory buffer that never blocks,
// so this failure is invisible to every other test in the package.
//
// What is asserted is the SLOT, not the response. A handler wedged in Write has
// produced nothing to inspect, and "the request ended" is exactly the weaker
// claim that would let the leak pass.
func TestStreamAbortsOnStalledReader(t *testing.T) {
	mcfg := mockupstream.DefaultConfig()
	// Enough content to overflow the socket buffers while the client reads nothing.
	mcfg.CompletionTokens = 50000

	cfg := streamingConfig()
	cfg.StreamWriteIdle = 200 * time.Millisecond
	// Both other bounds set far above it, so a pass here can only be the write
	// deadline. Without this the test would pass for the wrong reason.
	cfg.StreamIdleTimeout = 30 * time.Second
	cfg.StreamAbsoluteMax = 30 * time.Second
	h := newHarness(t, cfg, mcfg, nil)

	// A real server: only a real socket can apply a write deadline or exert
	// backpressure.
	srv := httptest.NewServer(h.proxy)
	defer srv.Close()

	addr := strings.TrimPrefix(srv.URL, "http://")
	conn, err := net.DialTimeout("tcp", addr, 2*time.Second)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer func() { _ = conn.Close() }()

	// A hand-rolled request, because every Go HTTP client drains the body for us —
	// and not draining is precisely the behavior under test.
	body := chatBody("gemini-2.5-flash", "stream this", true)
	req := fmt.Sprintf("POST /v1/chat/completions HTTP/1.1\r\nHost: %s\r\n"+
		"Content-Type: application/json\r\nContent-Length: %d\r\n\r\n%s",
		addr, len(body), body)
	if _, err = conn.Write([]byte(req)); err != nil {
		t.Fatalf("write request: %v", err)
	}

	// Read only the status line, then stop reading entirely. Reading nothing at all
	// would risk asserting against a request that never started.
	_ = conn.SetReadDeadline(time.Now().Add(3 * time.Second))
	status, err := bufio.NewReader(conn).ReadString('\n')
	if err != nil {
		t.Fatalf("read status line: %v", err)
	}
	if !strings.Contains(status, "200") {
		t.Fatalf("status line = %q, want 200", strings.TrimSpace(status))
	}

	testutil.RequireEventually(t, 3*time.Second, 10*time.Millisecond, func() bool {
		return testutil.CounterValue(t, h.metrics.inFlight) == 1
	}, "the stream should be holding the only slot")

	// From here the client reads nothing. The write deadline is the only thing that
	// can end this: the upstream is healthy, the socket is open, the context is not
	// done, and the other two bounds are 30s away.
	requireSlotReleased(t, h, 10*time.Second,
		"a stalled reader must not hold its admission slot — this is the leak the "+
			"per-write deadline exists to close")

	if got := testutil.LabeledCounterValue(t, h.metrics.streamAborts,
		modelLabels("gemini-2.5-flash", abortWriteIdle)...); got != 1 {
		t.Errorf("stream_aborts_total{reason=write_idle} = %v, want 1", got)
	}
}

// SetWriteDeadline must actually reach the socket on the real serving path.
//
// http.ResponseController walks the ResponseWriter's Unwrap chain to find one
// that supports deadlines, and returns ErrNotSupported when nothing does. A
// middleware that wraps the ResponseWriter without implementing Unwrap silently
// disables the stalled-reader protection: the stream keeps working, no test
// notices, and the leak returns.
//
// Pinned at the level the constraint holds — through http.Server, as main.go's
// mux serves it.
func TestWriteDeadlineSupportedOnRealServer(t *testing.T) {
	var (
		probeErr error
		probed   = make(chan struct{})
	)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		probeErr = http.NewResponseController(w).SetWriteDeadline(time.Now().Add(time.Minute))
		close(probed)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL, nil)
	if err != nil {
		t.Fatalf("build request: %v", err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request: %v", err)
	}
	_ = resp.Body.Close()
	<-probed

	if errors.Is(probeErr, http.ErrNotSupported) {
		t.Fatal("SetWriteDeadline is unsupported on the plain http.Server path. " +
			"If a ResponseWriter wrapper was added, give it Unwrap() http.ResponseWriter — " +
			"without it the streaming path cannot bound a stalled reader.")
	}
	if probeErr != nil {
		t.Fatalf("SetWriteDeadline: %v", probeErr)
	}
}

// An unsupported write deadline degrades rather than failing the stream.
//
// httptest.ResponseRecorder has no connection, so SetWriteDeadline returns
// ErrNotSupported — which is exactly the shape a ResponseWriter wrapper would
// produce in production. Refusing to serve would turn a missing SECONDARY
// protection into a total outage of streaming, so the stream must still work;
// only the stalled-reader case is lost.
//
// This is also what keeps every other recorder-based streaming test meaningful.
func TestUnsupportedWriteDeadlineStillStreams(t *testing.T) {
	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 4

	cfg := streamingConfig()
	cfg.StreamWriteIdle = 50 * time.Millisecond
	h := newHarness(t, cfg, mcfg, nil)

	rec := h.do(t, chatBody("gemini-2.5-flash", "stream this", true), nil)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 — an unsupported deadline must not fail the stream", rec.Code)
	}
	if !strings.HasSuffix(strings.TrimSpace(rec.Body.String()), "data: [DONE]") {
		t.Error("the stream must still complete normally")
	}
	if got := testutil.LabeledCounterValue(t, h.metrics.streamAborts,
		modelLabels("gemini-2.5-flash", abortWriteIdle)...); got != 0 {
		t.Errorf("stream_aborts_total{reason=write_idle} = %v, want 0 — nothing stalled", got)
	}
}

// countDataFrames counts SSE payload frames, excluding the terminator.
func countDataFrames(body string) int {
	var n int
	for _, line := range strings.Split(body, "\n") {
		payload, ok := strings.CutPrefix(strings.TrimSpace(line), "data: ")
		if ok && payload != "[DONE]" {
			n++
		}
	}
	return n
}

// The write deadline must survive otelhttp's ResponseWriter wrapper.
//
// This is the sibling of TestWriteDeadlineSupportedOnRealServer, and it exists
// because main.go wraps the completions route in otelhttp.NewHandler while that
// test covers only a bare http.Server. The two together say: the deadline works
// on the plain path, AND it works on the path production actually serves.
//
// Why it can break. writedeadline.go reaches the connection through
// http.ResponseController, which walks Unwrap() http.ResponseWriter to find a
// ResponseWriter that implements SetWriteDeadline. A wrapper without Unwrap ends
// that walk and SetWriteDeadline returns ErrNotSupported — at which point
// arm() degrades to a warning and a stalled reader can hold its admission slot
// until the inter-frame or absolute bound fires. Nothing else in the suite would
// notice, because every other streaming test uses httptest.ResponseRecorder,
// which has no connection and returns ErrNotSupported anyway.
//
// otelhttp happens to be safe today: it hands the handler a httpsnoop wrapper,
// and httpsnoop's generated wrappers all implement Unwrap. That is a third-party
// guarantee, not ours, so it is pinned here rather than trusted — a contrib
// upgrade that stops delegating through httpsnoop fails this test instead of
// silently disarming the protection in production.
//
// http.Flusher is asserted for the same reason: serveStreaming type-asserts it
// and 500s without it, so losing it would break streaming outright.
func TestWriteDeadlineSurvivesOtelHandler(t *testing.T) {
	var (
		probeErr   error
		gotFlusher bool
		probed     = make(chan struct{})
	)
	inner := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		probeErr = http.NewResponseController(w).SetWriteDeadline(time.Now().Add(time.Minute))
		_, gotFlusher = w.(http.Flusher)
		close(probed)
		w.WriteHeader(http.StatusOK)
	})
	// The same wrapping main.go applies to /v1/chat/completions.
	srv := httptest.NewServer(otelhttp.NewHandler(inner, "probe"))
	defer srv.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL, nil)
	if err != nil {
		t.Fatalf("build request: %v", err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request: %v", err)
	}
	_ = resp.Body.Close()
	<-probed

	if errors.Is(probeErr, http.ErrNotSupported) {
		t.Fatal("SetWriteDeadline is unsupported behind otelhttp.NewHandler. " +
			"The wrapper it hands the handler no longer implements " +
			"Unwrap() http.ResponseWriter, so writedeadline.go cannot reach the " +
			"connection and a stalled reader holds its admission slot until " +
			"STREAM_IDLE_TIMEOUT or STREAM_ABSOLUTE_MAX fires. Either pin the " +
			"previous contrib version or stop wrapping the streaming route.")
	}
	if probeErr != nil {
		t.Fatalf("SetWriteDeadline behind otelhttp: %v", probeErr)
	}
	if !gotFlusher {
		t.Fatal("http.Flusher is gone behind otelhttp.NewHandler; serveStreaming " +
			"type-asserts it and would 500 on every streaming request")
	}
}
