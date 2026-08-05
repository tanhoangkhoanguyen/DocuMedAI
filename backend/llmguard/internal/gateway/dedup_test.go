package gateway

// Characterization: singleflight request dedup.
// Harness and thresholds live in harness_test.go.

import (
	"fmt"
	"net/http"
	"sync"
	"testing"
	"time"

	"documedai/llmguard/internal/testutil"
	"documedai/llmguard/mockupstream"
)

// Identical concurrent requests coalesce into ONE upstream call and all callers
// receive the same response.
func TestDedupCoalescesConcurrentIdenticalRequests(t *testing.T) {
	const callers = 8

	cfg := realDefaults()

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 5
	// Hold the in-flight request open long enough that every caller arrives
	// while the leader is still waiting, which is what singleflight coalesces.
	//
	// This races goroutine start-up against the hold: a straggler that arrives
	// after the leader finishes starts its own flight, and the assertion below
	// sees 2 hits instead of 1. 400ms is ample on an idle laptop but thin on a
	// loaded CI runner with -race, where scheduling 8 goroutines can itself take
	// tens of milliseconds. 1s costs a fraction of a second and removes the
	// race — the assertion is about coalescing, not about how fast Go schedules.
	mcfg.Latency = time.Second
	h := newHarness(t, cfg, mcfg, nil)

	body := chatBody("gemini-2.5-flash", "coalesce me", false)

	var wg sync.WaitGroup
	bodies := make([]string, callers)
	codes := make([]int, callers)
	for i := range callers {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			rec := h.do(t, body, nil)
			codes[idx] = rec.Code
			bodies[idx] = rec.Body.String()
		}(i)
	}
	wg.Wait()

	if got := h.up.Hits(); got != 1 {
		t.Errorf("upstream hits = %d, want 1 — %d identical concurrent requests must coalesce",
			got, callers)
	}
	for i := range callers {
		if codes[i] != http.StatusOK {
			t.Errorf("caller %d: status = %d, want 200", i, codes[i])
		}
		if bodies[i] != bodies[0] {
			t.Errorf("caller %d got a different body than caller 0 — a shared flight must "+
				"hand back the same response", i)
		}
	}

	// The leader is counted alongside the 7 callers that piggy-backed, because
	// singleflight reports shared=true to every participant including the one
	// that did the work.
	//
	// That is accepted, not merely tolerated. The counter is a COALESCING SIGNAL
	// — "did a thundering herd collapse into one upstream call" — not a cost
	// meter, and LLMGuard's concern is reliability rather than spend. Deriving an
	// exact callers-saved figure would mean tracking waiters per key in a map,
	// buying arithmetic precision nobody reads with a mutex on the hot path and a
	// map that grows one entry per distinct request body.
	if got := testutil.CounterValue(t, h.metrics.dedupHits); got != float64(callers) {
		t.Errorf("dedupHits = %v, want %d (every participant counts, leader included)",
			got, callers)
	}
}

// A coalesced FAILURE is not a dedup hit.
//
// WAS A BUG, NOW FIXED: the counter was incremented before the error check, so
// callers that shared a flight which ended in a 500 — or a breaker-open 503, or
// a dropped connection — were all recorded as dedup hits. Nothing was shared
// except the failure.
//
// The counter reports that a thundering herd collapsed into one upstream call.
// A flight that produced no usable response absorbed nothing, and counting it
// would show the herd as handled while every caller was in fact failing.
func TestDedupDoesNotCountCoalescedFailure(t *testing.T) {
	const callers = 8

	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond // timing only
	cfg.RetryMaxDly = 5 * time.Millisecond

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusInternalServerError
	// Same reasoning as the coalescing test above: hold the flight open long
	// enough that every caller arrives while the leader is still in it.
	mcfg.Latency = time.Second
	h := newHarness(t, cfg, mcfg, nil)

	body := chatBody("gemini-2.5-flash", "coalesce this failure", false)

	var wg sync.WaitGroup
	codes := make([]int, callers)
	for i := range callers {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			codes[idx] = h.do(t, body, nil).Code
		}(i)
	}
	wg.Wait()

	// Precondition: the callers really did share one flight. Without this the
	// dedupHits assertion below would pass trivially.
	//
	// The expected count is RetryMax, not 1: Hits() counts upstream ATTEMPTS,
	// and 500 is retryable, so the single shared flight burns its whole retry
	// budget inside doWithRetry. Eight independent flights would be 32.
	if got, want := h.up.Hits(), int64(cfg.RetryMax); got != want {
		t.Fatalf("upstream hits = %d, want %d (one shared flight x RetryMax) — the "+
			"callers must share one flight for this test to mean anything", got, want)
	}
	for i := range callers {
		if codes[i] != http.StatusInternalServerError {
			t.Errorf("caller %d: status = %d, want 500", i, codes[i])
		}
	}

	if got := testutil.CounterValue(t, h.metrics.dedupHits); got != 0 {
		t.Errorf("dedupHits = %v, want 0 — a shared flight that failed delivered "+
			"nothing to share", got)
	}
}

// A different body is a different dedup key, so nothing coalesces.
func TestDedupDoesNotCoalesceDifferentBodies(t *testing.T) {
	const callers = 4

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 3
	// Unlike the coalescing test above, this one does not race the clock: four
	// distinct bodies are four distinct dedup keys, so they never share a flight
	// no matter when each caller arrives. The latency only keeps the requests
	// genuinely concurrent; its exact value cannot change the outcome.
	mcfg.Latency = 200 * time.Millisecond
	h := newHarness(t, realDefaults(), mcfg, nil)

	var wg sync.WaitGroup
	for i := range callers {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			h.do(t, chatBody("gemini-2.5-flash", fmt.Sprintf("distinct prompt %d", idx), false), nil)
		}(i)
	}
	wg.Wait()

	if got := h.up.Hits(); got != callers {
		t.Errorf("upstream hits = %d, want %d — distinct bodies must not coalesce", got, callers)
	}
	if got := testutil.CounterValue(t, h.metrics.dedupHits); got != 0 {
		t.Errorf("dedupHits = %v, want 0", got)
	}
}
