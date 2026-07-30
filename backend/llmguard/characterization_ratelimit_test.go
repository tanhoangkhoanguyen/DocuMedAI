package main

// Characterization: rate-limit shedding.
// Harness and thresholds live in characterization_helpers_test.go.

import (
	"net/http"
	"testing"
	"time"

	"documedai/llmguard/internal/testutil"
	"documedai/llmguard/mockupstream"
)

// Needs a real Redis: shedding requires a live token bucket that can return
// "no token". With Redis unreachable the limiter fails OPEN and never sheds,
// so this path cannot be reached offline. Skips when Redis is absent; CI
// provides one.
func TestCharacterizeRateLimitShedding(t *testing.T) {
	rdb := testutil.RequireRedis(t)

	cfg := realDefaults()
	// A bucket of exactly one token, refilling at 1/s, and a short wait so the
	// test does not sit for the production 5s.
	cfg.RateLimitRPM = 60
	cfg.RateLimitBurst = 1
	cfg.RateWaitMax = 150 * time.Millisecond

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 3
	h := newHarness(t, cfg, mcfg, newRateLimiter(rdb, cfg.RateLimitRPM, cfg.RateLimitBurst))

	// The bucket is keyed on (api-key hint + model); a unique key keeps this run
	// independent of anything already in the DB.
	headers := map[string]string{"Authorization": "Bearer sk-test-" + t.Name()}
	body := chatBody("gemini-2.5-flash", "rate limit me", false)

	first := h.do(t, body, headers)
	if first.Code != http.StatusOK {
		t.Fatalf("first request status = %d, want 200 (bucket starts full)\nbody: %s",
			first.Code, first.Body.String())
	}

	second := h.do(t, body, headers)
	if second.Code != http.StatusTooManyRequests {
		t.Fatalf("second request status = %d, want 429 (bucket exhausted)\nbody: %s",
			second.Code, second.Body.String())
	}

	env := decodeError(t, second)
	if env.Error.Type != "rate_limit" {
		t.Errorf("error.type = %q, want rate_limit", env.Error.Type)
	}
	if env.Error.Message != "proxy rate limit exceeded" {
		t.Errorf("error.message = %q", env.Error.Message)
	}

	// Shedding happens BEFORE the provider is called: the shed request must not
	// have reached upstream.
	if h.up.Hits() != 1 {
		t.Errorf("upstream hits = %d, want 1 (the 429 is shed before dispatch)", h.up.Hits())
	}
	if got := testutil.LabeledCounterValue(t, h.metrics.rateLimited, "gemini-2.5-flash"); got != 1 {
		t.Errorf("rateLimited metric = %v, want 1", got)
	}
}
