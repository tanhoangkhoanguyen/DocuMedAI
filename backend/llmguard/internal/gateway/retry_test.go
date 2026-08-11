package gateway

// Characterization: the retry loop — attempt count, recovery, and Retry-After.
// Harness and thresholds live in harness_test.go.

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"documedai/llmguard/internal/testutil"
	"documedai/llmguard/mockupstream"
	"documedai/llmguard/provider"
)

// Pins RetryMax=4: a permanently failing upstream is called exactly four times
// (1 initial + 3 retries), then the vendor's error is surfaced to the caller.
func TestRetryExhaustsAtRetryMax(t *testing.T) {
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
	if got := testutil.LabeledCounterValue(t, h.metrics.retries, modelLabels("gemini-2.5-flash")...); got != 3 {
		t.Errorf("retries metric = %v, want 3 (RetryMax-1)", got)
	}
}

// Retry-then-succeed, driven by mockupstream's outage window: the first
// attempts land inside the outage and fail, a later one lands after it and
// succeeds. Retries are NOT a fresh request as far as the mock is concerned —
// an identical body yields an identical verdict — so a time-boxed outage is the
// mechanism that makes a retry observably different from its predecessor.
func TestRetryThenSucceed(t *testing.T) {
	cfg := realDefaults()
	cfg.RetryBaseDly = 200 * time.Millisecond
	cfg.RetryMaxDly = 2 * time.Second

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 3
	h := newHarness(t, cfg, mcfg, nil)

	// The outage must end AFTER attempt 2 and BEFORE attempt 3, so attempts 0-2
	// fail and attempt 3 succeeds. Both edges can flake, in opposite ways:
	//
	//   too short -> attempt 2 lands after the window and succeeds early. The
	//                test still passes, but proves nothing about recovery.
	//   too long  -> attempt 3 lands inside the window, all four fail, and the
	//                test goes red for a scheduling reason unrelated to retry.
	//
	// backoffDelay is deterministic (FNV of seed+attempt, no global rand), but
	// the seed is the dedup key — the sha256 of the request body — so a test
	// that changes its prompt draws different jitter. Measured across 200 seeds:
	//
	//   attempt 2 lands in [621ms, 727ms]
	//   attempt 3 lands in [1452ms, 1694ms]
	//
	// 1100ms is the midpoint of the safe corridor: >=373ms of slack after
	// attempt 2 and >=352ms before attempt 3, versus 1s which is lopsided
	// (273ms / 452ms). Balanced margin is what survives a loaded runner, since
	// the same slowness that delays attempt 3 also delays attempt 2.
	h.up.mock.StartOutage(1100 * time.Millisecond)

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
	if got := testutil.LabeledCounterValue(t, h.metrics.retries, modelLabels("gemini-2.5-flash")...); got < 1 {
		t.Errorf("retries metric = %v, want at least 1", got)
	}
}

// A transport failure on a later attempt must not erase a real upstream error
// from an earlier one.
//
// The retry loop remembers the most INFORMATIVE failure rather than the most
// recent. Under plain last-wins, the dropped connection on attempt 2+ would
// overwrite attempt 1's translated 500, serveBuffered's errors.As would find no
// *UpstreamError, and the caller would get a generic 503 "upstream unavailable"
// instead of the vendor's actual status and message.
func TestRetryKeepsMostInformativeError(t *testing.T) {
	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond // timing only
	cfg.RetryMaxDly = 5 * time.Millisecond

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusInternalServerError

	// Attempt 1 gets the mock's real 500 (translated into an *UpstreamError
	// carrying the vendor's message). Every later attempt has its connection
	// hijacked and closed without a response, which surfaces as a transport
	// error with no status to translate.
	var attempts atomic.Int64
	h := newHarnessWithHandler(t, cfg, mcfg, nil,
		func(mock http.Handler, w http.ResponseWriter, r *http.Request) {
			if attempts.Add(1) == 1 {
				mock.ServeHTTP(w, r)
				return
			}
			conn, _, err := w.(http.Hijacker).Hijack()
			if err != nil {
				t.Errorf("hijack: %v", err)
				return
			}
			_ = conn.Close() // no response at all — the client sees EOF
		})

	rec := h.do(t, chatBody("gemini-2.5-flash", "informative error", false), nil)

	// The vendor's own 500 survives, rather than being masked as a 503.
	if rec.Code != http.StatusInternalServerError {
		t.Errorf("status = %d, want 500 — attempt 1's upstream error must survive "+
			"the later transport failures\nbody: %s", rec.Code, rec.Body.String())
	}
	env := decodeError(t, rec)
	if env.Error.Message == "upstream unavailable" {
		t.Error("got the generic transport message; the vendor's error was overwritten")
	}
	if !strings.Contains(env.Error.Message, "injected") {
		t.Errorf("error.message = %q, want the mock's own message", env.Error.Message)
	}
	if env.Error.Type != "upstream_error" {
		t.Errorf("error.type = %q, want upstream_error", env.Error.Type)
	}

	// All four attempts ran: a transport failure is still retryable.
	if got := attempts.Load(); got != 4 {
		t.Errorf("attempts = %d, want 4 (transport failures keep retrying)", got)
	}
}

// Retry-After from the upstream is honored in place of exponential backoff.
func TestRetryAfterIsHonored(t *testing.T) {
	cfg := realDefaults()
	// Backoff would be ~1ms per gap; Retry-After: 1 should dominate, making the
	// whole call take at least a second.
	//
	// RetryMaxDly is the ceiling Retry-After is clamped to, so it must exceed
	// the 1s the upstream asks for — otherwise this would measure the clamp
	// rather than whether Retry-After is honored at all.
	cfg.RetryBaseDly = time.Millisecond
	cfg.RetryMaxDly = 5 * time.Second
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
	// Asserted at 900ms rather than a full second. The point is that Retry-After
	// (1s) dominated the ~1ms backoff, and anything near a second proves that —
	// but timer granularity and rounding can land a correct run a hair under
	// 1000ms, which would fail an exact-boundary check for no real reason.
	// Backoff alone would finish in single-digit milliseconds, so 900ms is
	// nowhere near ambiguous.
	if elapsed < 900*time.Millisecond {
		t.Errorf("elapsed = %s; Retry-After: 1 must override the ~1ms backoff", elapsed)
	}
}

// Retry-After is capped at RetryMaxDly. An upstream asking for a day must not
// be able to park the request for one, holding a connection and a singleflight
// slot the whole time.
func TestRetryAfterIsCapped(t *testing.T) {
	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond
	cfg.RetryMaxDly = 50 * time.Millisecond // the ceiling under test
	cfg.RetryMax = 2                        // one gap

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusTooManyRequests
	mcfg.RetryAfter = 86400 // a full day
	h := newHarness(t, cfg, mcfg, nil)

	start := time.Now()
	rec := h.do(t, chatBody("gemini-2.5-flash", "absurd retry-after", false), nil)
	elapsed := time.Since(start)

	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want 429", rec.Code)
	}
	if h.up.Hits() != 2 {
		t.Errorf("upstream hits = %d, want 2", h.up.Hits())
	}
	// Generous ceiling: the point is "seconds, not a day", not exact timing.
	if elapsed > 5*time.Second {
		t.Errorf("elapsed = %s; Retry-After: 86400 must be capped at RetryMaxDly", elapsed)
	}
}

// parseRetryAfter accepts both RFC 9110 forms and rejects everything unusable.
// Google emits the date form, so ignoring it would fall back to plain backoff
// exactly when the provider was most specific about pacing.
func TestParseRetryAfterForms(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want time.Duration
	}{
		{name: "delta-seconds", in: "120", want: 2 * time.Minute},
		{name: "delta-seconds with spaces", in: "  30  ", want: 30 * time.Second},
		{name: "zero means no wait", in: "0", want: 0},
		{name: "negative is ignored", in: "-5", want: 0},
		{name: "empty", in: "", want: 0},
		{name: "garbage", in: "soon", want: 0},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := parseRetryAfter(tc.in); got != tc.want {
				t.Errorf("parseRetryAfter(%q) = %v, want %v", tc.in, got, tc.want)
			}
		})
	}

	// Dates are relative to now, so assert a range rather than an exact value.
	t.Run("HTTP-date in the future", func(t *testing.T) {
		in := time.Now().Add(2 * time.Minute).UTC().Format(http.TimeFormat)
		got := parseRetryAfter(in)
		if got <= time.Minute || got > 2*time.Minute {
			t.Errorf("parseRetryAfter(%q) = %v, want ~2m", in, got)
		}
	})

	t.Run("HTTP-date in the past is not a wait", func(t *testing.T) {
		in := time.Now().Add(-time.Hour).UTC().Format(http.TimeFormat)
		if got := parseRetryAfter(in); got > 0 {
			t.Errorf("parseRetryAfter(%q) = %v, want <= 0", in, got)
		}
	})
}

// The upstream's Retry-After reaches the CLIENT, not just the retry loop.
//
// Without it a caller facing a 429 has to guess when to come back, which is how
// a thundering herd re-forms the moment quota frees up. The header rides on
// *UpstreamError because that is the only thing surviving the breaker and
// deduper on the failure path — the upstreamResult holding the response headers
// is discarded there.
func TestRetryAfterIsForwardedToClient(t *testing.T) {
	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond
	cfg.RetryMaxDly = 5 * time.Millisecond
	cfg.RetryMax = 1 // no retry: we only care about what reaches the client

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusTooManyRequests
	mcfg.RetryAfter = 7
	h := newHarness(t, cfg, mcfg, nil)

	rec := h.do(t, chatBody("gemini-2.5-flash", "pace me", false), nil)

	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want 429\nbody: %s", rec.Code, rec.Body.String())
	}
	if got := rec.Header().Get("Retry-After"); got != "7" {
		t.Errorf("Retry-After = %q, want %q — the vendor's pacing hint must reach the client", got, "7")
	}
}

// A 429 with no Retry-After must not grow one.
func TestNoRetryAfterHeaderWhenUpstreamSendsNone(t *testing.T) {
	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond
	cfg.RetryMaxDly = 5 * time.Millisecond
	cfg.RetryMax = 1

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusTooManyRequests
	// mcfg.RetryAfter left at 0 — the mock emits no header.
	h := newHarness(t, cfg, mcfg, nil)

	rec := h.do(t, chatBody("gemini-2.5-flash", "no hint", false), nil)

	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want 429", rec.Code)
	}
	if got := rec.Header().Get("Retry-After"); got != "" {
		t.Errorf("Retry-After = %q, want absent — we must not invent a pacing hint", got)
	}
}

// Giving up mid-backoff must not discard the vendor's error.
//
// Preserving the most informative failure through the loop is pointless if the
// context-cancellation exit replaces it with "context deadline exceeded". The
// two causes differ: a client that hung up (Canceled) gets ctx.Err(), but a
// deadline still has a caller listening, and the vendor's 429 is strictly
// better information than "deadline exceeded".
func TestDeadlineKeepsUpstreamError(t *testing.T) {
	cfg := realDefaults()
	// Backoff far longer than the deadline, so the deadline fires mid-sleep.
	cfg.RetryBaseDly = 500 * time.Millisecond
	cfg.RetryMaxDly = 2 * time.Second

	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()

	attempts := 0
	_, err := doWithRetry(ctx, cfg, "seed", func() {}, func(context.Context) (*upstreamResult, error) {
		attempts++
		ue := &provider.UpstreamError{
			Status: http.StatusTooManyRequests,
			Body:   provider.NewErrorEnvelope("quota exceeded", "rate_limit"),
		}
		return &upstreamResult{status: http.StatusTooManyRequests, header: http.Header{}}, ue
	})

	var ue *provider.UpstreamError
	if !errors.As(err, &ue) {
		t.Fatalf("err = %v; the vendor's 429 must survive the deadline, "+
			"otherwise the caller emits a generic 503", err)
	}
	if ue.Status != http.StatusTooManyRequests {
		t.Errorf("status = %d, want 429", ue.Status)
	}
	if attempts != 1 {
		t.Errorf("attempts = %d, want 1 (the deadline fires during the first backoff)", attempts)
	}
}

// A client that hangs up gets context.Canceled: nobody is waiting for a
// response, so why we stopped is the honest answer.
func TestClientCancelReportsCancellation(t *testing.T) {
	cfg := realDefaults()
	cfg.RetryBaseDly = 500 * time.Millisecond
	cfg.RetryMaxDly = 2 * time.Second

	ctx, cancel := context.WithCancel(context.Background())

	_, err := doWithRetry(ctx, cfg, "seed", func() {}, func(context.Context) (*upstreamResult, error) {
		cancel() // the client disconnects during the first attempt
		ue := &provider.UpstreamError{
			Status: http.StatusTooManyRequests,
			Body:   provider.NewErrorEnvelope("quota exceeded", "rate_limit"),
		}
		return &upstreamResult{status: http.StatusTooManyRequests, header: http.Header{}}, ue
	})

	if !errors.Is(err, context.Canceled) {
		t.Errorf("err = %v, want context.Canceled — a disconnected client is why we stopped", err)
	}
}
