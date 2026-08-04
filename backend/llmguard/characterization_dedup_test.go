package main

// Characterization: singleflight request dedup.
// Harness and thresholds live in characterization_helpers_test.go.

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
func TestCharacterizeDedupCoalescesConcurrentIdenticalRequests(t *testing.T) {
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

	// QUIRK (pinned, not fixed): singleflight reports shared=true to EVERY
	// participant including the leader that did the work, so the dedup-hit
	// counter records all 8 callers rather than the 7 that piggy-backed.
	if got := testutil.CounterValue(t, h.metrics.dedupHits); got != float64(callers) {
		t.Errorf("dedupHits = %v, want %d (today the leader is counted too)", got, callers)
	}
}

// A different body is a different dedup key, so nothing coalesces.
func TestCharacterizeDedupDoesNotCoalesceDifferentBodies(t *testing.T) {
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
