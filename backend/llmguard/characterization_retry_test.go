package main

// Characterization: the retry loop — attempt count, recovery, and Retry-After.
// Harness and thresholds live in characterization_helpers_test.go.

import (
	"net/http"
	"strings"
	"testing"
	"time"

	"documedai/llmguard/internal/testutil"
	"documedai/llmguard/mockupstream"
)

// Pins RetryMax=4: a permanently failing upstream is called exactly four times
// (1 initial + 3 retries), then the vendor's error is surfaced to the caller.
func TestCharacterizeRetryExhaustsAtRetryMax(t *testing.T) {
	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond // timing only; attempt COUNT is the real 4
	cfg.RetryMaxDly = 5 * time.Millisecond

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusInternalServerError
	h := newHarness(t, cfg, mcfg, nil)

	rec := h.do(t, chatBody("gemini-2.5-flash", "always fails", false), nil)

	if got := h.up.Hits(); got != 4 {
		t.Errorf("upstream hits = %d, want 4 (RetryMax)", got)
	}
	// The upstream's own status and message are passed through, NOT masked as 503.
	if rec.Code != http.StatusInternalServerError {
		t.Errorf("status = %d, want 500 (upstream status is surfaced)", rec.Code)
	}
	env := decodeError(t, rec)
	if !strings.Contains(env.Error.Message, "injected") {
		t.Errorf("error.message = %q, want the upstream's own message", env.Error.Message)
	}
	if env.Error.Type != "upstream_error" {
		t.Errorf("error.type = %q, want upstream_error", env.Error.Type)
	}
	// retries metric counts attempts beyond the first.
	if got := testutil.LabeledCounterValue(t, h.metrics.retries, "gemini-2.5-flash"); got != 3 {
		t.Errorf("retries metric = %v, want 3 (RetryMax-1)", got)
	}
}

// Retry-then-succeed, driven by mockupstream's outage window: the first
// attempts land inside the outage and fail, a later one lands after it and
// succeeds. Retries are NOT a fresh request as far as the mock is concerned —
// an identical body yields an identical verdict — so a time-boxed outage is the
// mechanism that makes a retry observably different from its predecessor.
func TestCharacterizeRetryThenSucceed(t *testing.T) {
	cfg := realDefaults()
	// Attempts land at roughly 0ms, 200-250ms, 600-750ms, 1400-1750ms.
	cfg.RetryBaseDly = 200 * time.Millisecond
	cfg.RetryMaxDly = 2 * time.Second

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 3
	h := newHarness(t, cfg, mcfg, nil)

	// A 1s outage covers the first three attempts with ~400ms of margin before
	// the fourth.
	h.up.mock.StartOutage(time.Second)

	start := time.Now()
	rec := h.do(t, chatBody("gemini-2.5-flash", "recovers", false), nil)
	elapsed := time.Since(start)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 (a later retry should land after the outage)\n"+
			"elapsed=%s hits=%d body: %s", rec.Code, elapsed, h.up.Hits(), rec.Body.String())
	}
	got := decodeChat(t, rec)
	if len(got.Choices) != 1 || got.Choices[0].Message.Content == "" {
		t.Errorf("expected a real completion after recovery, got %+v", got)
	}
	if h.up.Hits() < 2 {
		t.Errorf("upstream hits = %d, want at least 2 (failure then success)", h.up.Hits())
	}
	if h.up.Hits() > 4 {
		t.Errorf("upstream hits = %d, must never exceed RetryMax=4", h.up.Hits())
	}
	if got := testutil.LabeledCounterValue(t, h.metrics.retries, "gemini-2.5-flash"); got < 1 {
		t.Errorf("retries metric = %v, want at least 1", got)
	}
}

// Retry-After from the upstream is honored in place of exponential backoff.
func TestCharacterizeRetryAfterIsHonored(t *testing.T) {
	cfg := realDefaults()
	// Backoff would be ~1ms per gap; Retry-After: 1 should dominate, making the
	// whole call take at least a second.
	cfg.RetryBaseDly = time.Millisecond
	cfg.RetryMaxDly = 2 * time.Millisecond
	cfg.RetryMax = 2 // one gap, so the test waits ~1s rather than ~3s

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusTooManyRequests
	mcfg.RetryAfter = 1 // seconds
	h := newHarness(t, cfg, mcfg, nil)

	start := time.Now()
	rec := h.do(t, chatBody("gemini-2.5-flash", "slow down", false), nil)
	elapsed := time.Since(start)

	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want 429", rec.Code)
	}
	if h.up.Hits() != 2 {
		t.Errorf("upstream hits = %d, want 2", h.up.Hits())
	}
	if elapsed < time.Second {
		t.Errorf("elapsed = %s; Retry-After: 1 must override the ~1ms backoff", elapsed)
	}
}
