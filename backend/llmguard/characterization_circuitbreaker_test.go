package main

// Characterization: the circuit breaker.
// Harness and thresholds live in characterization_helpers_test.go.

import (
	"net/http"
	"testing"
	"time"

	"documedai/llmguard/internal/testutil"
	"documedai/llmguard/mockupstream"
)

// Pins CircuitMinReqs=10 and CircuitFailRatio=0.6. The breaker wraps the WHOLE
// retry loop, so one client request is ONE breaker observation regardless of how
// many upstream attempts it burns.
func TestCharacterizeCircuitBreakerTrips(t *testing.T) {
	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond // timing only
	cfg.RetryMaxDly = 5 * time.Millisecond

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusInternalServerError
	h := newHarness(t, cfg, mcfg, nil)

	body := chatBody("gemini-2.5-flash", "sustained failure", false)

	// Sequential, so each request is its own singleflight flight and its own
	// breaker observation. Concurrent identical requests would coalesce.
	for i := 1; i <= 10; i++ {
		rec := h.do(t, body, nil)
		if rec.Code != http.StatusInternalServerError {
			t.Fatalf("request %d: status = %d, want 500 while the breaker is still closed", i, rec.Code)
		}
	}

	// 10 requests x 4 attempts, all reaching upstream.
	hitsBeforeTrip := h.up.Hits()
	if hitsBeforeTrip != 40 {
		t.Errorf("upstream hits before trip = %d, want 40 (10 requests x RetryMax 4)", hitsBeforeTrip)
	}

	// The 11th observation finds the breaker open.
	rec := h.do(t, body, nil)
	if rec.Code != http.StatusServiceUnavailable {
		t.Errorf("status after trip = %d, want 503", rec.Code)
	}
	env := decodeError(t, rec)
	if env.Error.Message != "upstream unavailable" {
		t.Errorf("error.message = %q, want %q (breaker-open is NOT the vendor message)",
			env.Error.Message, "upstream unavailable")
	}
	if env.Error.Type != "upstream_error" {
		t.Errorf("error.type = %q, want upstream_error", env.Error.Type)
	}

	// The whole point: an open breaker stops calling upstream entirely.
	if got := h.up.Hits(); got != hitsBeforeTrip {
		t.Errorf("upstream hits = %d, want %d — an open breaker must not dispatch",
			got, hitsBeforeTrip)
	}
	if got := testutil.CounterValue(t, h.metrics.circuitState); got != 2 {
		t.Errorf("circuitState gauge = %v, want 2 (open)", got)
	}
}
