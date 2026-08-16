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
