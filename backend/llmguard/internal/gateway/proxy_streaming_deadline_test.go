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
	"net/http"
	"strings"
	"testing"
	"time"

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
