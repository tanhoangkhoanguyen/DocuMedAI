package main

import (
	"context"
	"errors"
	"hash/fnv"
	"math"
	"net/http"
	"strconv"
	"time"

	"github.com/sony/gobreaker"

	"documedai/llmguard/provider"
)

// upstreamResult is the buffered outcome of one upstream call. `body` is the
// NORMALIZED (OpenAI-shaped) response, already translated by the provider — we
// buffer it rather than streaming to the client ONLY on the retry path so a
// failed attempt can be discarded and replayed. Streaming responses bypass this
// and are handled separately in proxy.go.
type upstreamResult struct {
	status int
	header http.Header
	body   []byte
	usage  provider.Usage
}

// retryStatuses are the HTTP statuses worth retrying — transient upstream
// problems, not client errors. 429 = rate limited by the provider itself.
func isRetryable(status int) bool {
	switch status {
	case http.StatusTooManyRequests, // 429
		http.StatusInternalServerError, // 500
		http.StatusBadGateway,          // 502
		http.StatusServiceUnavailable,  // 503
		http.StatusGatewayTimeout:      // 504
		return true
	}
	return false
}

// newBreaker builds the circuit breaker that wraps every upstream call. When
// the provider is failing hard, the breaker OPENS and we fail fast with 503
// instead of piling on more doomed requests — protecting both upstream and our
// own latency.
func newBreaker(cfg Config, m *Metrics) *gobreaker.CircuitBreaker {
	return gobreaker.NewCircuitBreaker(gobreaker.Settings{
		Name:    cfg.Provider + "-upstream",
		Timeout: cfg.CircuitOpenFor, // how long to stay open before half-open probe
		ReadyToTrip: func(c gobreaker.Counts) bool {
			if c.Requests < cfg.CircuitMinReqs {
				return false // need a minimum sample before tripping
			}
			ratio := float64(c.TotalFailures) / float64(c.Requests)
			return ratio >= cfg.CircuitFailRatio
		},
		// Decide what counts as an upstream failure. Without this, gobreaker's
		// default treats EVERY non-nil error as one — including a 400 caused
		// entirely by the caller's own malformed request. A single buggy client
		// could then open the breaker for every other user for CircuitOpenFor.
		//
		// The breaker exists to detect a sick PROVIDER, so only the statuses
		// retry already treats as transient (429/5xx) may trip it. A
		// non-retryable status means the request was bad, not the upstream.
		IsSuccessful: func(err error) bool {
			if err == nil {
				return true
			}
			var ue *provider.UpstreamError
			if errors.As(err, &ue) {
				return !isRetryable(ue.Status)
			}
			// No status to judge — a transport failure, timeout or context
			// cancellation. That IS an upstream problem.
			return false
		},
		OnStateChange: func(_ string, _ gobreaker.State, to gobreaker.State) {
			// Surface breaker state as a gauge for dashboards/alerts.
			switch to {
			case gobreaker.StateClosed:
				m.circuitState.Set(0)
			case gobreaker.StateHalfOpen:
				m.circuitState.Set(1)
			case gobreaker.StateOpen:
				m.circuitState.Set(2)
			}
		},
	})
}

// doWithRetry runs `call` up to cfg.RetryMax times with exponential backoff +
// jitter, honoring an upstream Retry-After header when present. It returns the
// last result; the caller decides what to send downstream.
//
// `attempt` is reported back via onRetry so metrics can count burned attempts.
func doWithRetry(
	ctx context.Context,
	cfg Config,
	seed string, // used to derive deterministic-but-spread jitter (no global rand)
	onRetry func(),
	call func(ctx context.Context) (*upstreamResult, error),
) (*upstreamResult, error) {
	var last *upstreamResult
	var lastErr error

	for attempt := 0; attempt < cfg.RetryMax; attempt++ {
		if attempt > 0 {
			onRetry()
		}

		res, err := call(ctx)
		if err == nil {
			return res, nil // success
		}

		// A translated upstream error carries the vendor's status. When that
		// status is not retryable — 400, 401, 404 — the request will fail
		// identically on every attempt, so retrying only burns quota and
		// latency. Bail out and let the caller surface the vendor's own error.
		//
		// Anything else (transport failure, BuildRequest, marshal) carries no
		// status and IS worth retrying: a dropped connection is exactly the
		// case retry exists for.
		var ue *provider.UpstreamError
		if errors.As(err, &ue) && !isRetryable(ue.Status) {
			return res, err
		}
		last, lastErr = res, err

		// Don't sleep after the final attempt.
		if attempt == cfg.RetryMax-1 {
			break
		}

		delay := backoffDelay(cfg, attempt, seed)
		if res != nil {
			if ra := parseRetryAfter(res.header.Get("Retry-After")); ra > 0 {
				delay = ra // upstream told us exactly how long to wait — respect it
			}
		}
		select {
		case <-ctx.Done():
			return last, ctx.Err()
		case <-time.After(delay):
		}
	}
	if lastErr != nil {
		return last, lastErr
	}
	return last, errors.New("exhausted retries")
}

// backoffDelay = base * 2^attempt, capped, plus deterministic jitter derived
// from `seed`. We avoid math/rand global state so behavior is reproducible and
// concurrent callers with different request bodies still spread out.
func backoffDelay(cfg Config, attempt int, seed string) time.Duration {
	exp := float64(cfg.RetryBaseDly) * math.Pow(2, float64(attempt))
	if exp > float64(cfg.RetryMaxDly) {
		exp = float64(cfg.RetryMaxDly)
	}
	// jitter in [0, 0.25*exp) derived from a hash of the seed+attempt
	h := fnv.New32a()
	_, _ = h.Write([]byte(seed))
	_, _ = h.Write([]byte{byte(attempt)})
	frac := float64(h.Sum32()%1000) / 1000.0 // 0..0.999
	jitter := frac * 0.25 * exp
	return time.Duration(exp + jitter)
}

// parseRetryAfter handles the delta-seconds form of Retry-After. HTTP-date form
// is ignored (returns 0) — backoff covers it.
func parseRetryAfter(v string) time.Duration {
	if v == "" {
		return 0
	}
	if secs, err := strconv.Atoi(v); err == nil && secs >= 0 {
		return time.Duration(secs) * time.Second
	}
	return 0
}
