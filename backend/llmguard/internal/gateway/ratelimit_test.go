package gateway

// Characterization: rate-limit shedding.
// Harness and thresholds live in harness_test.go.

import (
	"context"
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
func TestRateLimitShedding(t *testing.T) {
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

// Acquire honours RateWaitMax as a real bound.
//
// WAS A BUG, NOW FIXED: the deadline was checked BEFORE the sleep and the sleep
// was a fixed interval, so a caller could be held up to one full poll interval
// past maxWait. RateWaitMax is the knob an operator turns to bound tail latency,
// so overshooting it silently defeats its purpose.
//
// The overshoot scales with the poll interval — (1/rate)/4, floored at 20ms — so
// it is invisible at the production 480 RPM (31ms) and large at a low RPM. This
// test uses 12 RPM, where the interval is 1.25s against a 300ms budget: without
// the clamp the very first sleep alone blows the deadline by ~4x.
func TestRateLimitAcquireRespectsWaitDeadline(t *testing.T) {
	rdb := testutil.RequireRedis(t)

	const (
		rpm     = 12                     // → 0.2 tokens/s → 1.25s poll interval
		maxWait = 300 * time.Millisecond // deliberately shorter than one interval
	)
	limiter := newRateLimiter(rdb, rpm, 1)

	// A key unique to this run, so nothing already in the DB affects it.
	key := "deadline-test:" + t.Name()

	// Drain the single token. The bucket starts full, so this must succeed.
	if !limiter.Acquire(context.Background(), key, maxWait) {
		t.Fatal("first Acquire returned false; the bucket starts full")
	}

	// The bucket is empty and refills at 0.2/s, so no token can appear within
	// maxWait — this call is guaranteed to run out the clock.
	began := time.Now()
	granted := limiter.Acquire(context.Background(), key, maxWait)
	elapsed := time.Since(began)

	if granted {
		t.Fatalf("second Acquire granted a token after %v; the bucket refills at "+
			"%.2f/s and cannot produce one within %v", elapsed, float64(rpm)/60, maxWait)
	}

	// Tolerance covers scheduling and the Redis round trip, and is far below the
	// 1.25s a single unclamped poll would add.
	const tolerance = 150 * time.Millisecond
	if elapsed > maxWait+tolerance {
		t.Errorf("Acquire took %v, want <= %v — it must not sleep past its own deadline",
			elapsed, maxWait+tolerance)
	}
}
