package gateway

import (
	"context"
	"errors"
	"hash/fnv"
	"math"
	"net/http"
	"strconv"
	"strings"
	"sync"
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

// isRetryable reports whether a status is worth another attempt — a transient
// upstream problem, not a client error. 429 = rate limited by the provider
// itself. Also decides what may trip the circuit breaker; see newBreaker.
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

// isUpstreamErr reports whether err is a translated upstream error — one that
// carries the vendor's own status and message. Anything else (transport
// failure, timeout, encode error) says only "the call did not complete".
func isUpstreamErr(err error) bool {
	var ue *provider.UpstreamError
	return errors.As(err, &ue)
}

// breakerGroup holds one circuit breaker per provider.
//
// Isolation is the point: with a single shared breaker, one sick upstream trips
// the circuit for every other upstream too, so a Vertex outage would 503 traffic
// bound for an entirely healthy openai-compat endpoint. That is the opposite of
// what routing a model to a second provider is meant to buy.
//
// Breakers are created on demand rather than pre-seeded from the config, because
// the registry — not this type — is the authority on which providers exist, and
// a breaker for a provider that never serves a request would report a permanent
// "closed" for something that does not run.
type breakerGroup struct {
	mu       sync.Mutex
	cfg      Config
	metrics  *Metrics
	breakers map[string]*gobreaker.CircuitBreaker
	// sharer propagates trips to other replicas. Nil in a single-replica
	// deployment and throughout the test suite, where it is a no-op — see
	// breakershare.go.
	sharer *BreakerSharer
}

func newBreakerGroup(cfg Config, m *Metrics, sharer *BreakerSharer) *breakerGroup {
	return &breakerGroup{
		cfg:      cfg,
		metrics:  m,
		breakers: map[string]*gobreaker.CircuitBreaker{},
		sharer:   sharer,
	}
}

// openElsewhere reports whether another replica has recently found this provider
// unhealthy. Kept on breakerGroup so proxy.go asks one thing about breaker state
// rather than reaching into the sharer itself.
func (g *breakerGroup) openElsewhere(ctx context.Context, providerName string) bool {
	return g.sharer.isOpenElsewhere(ctx, providerName)
}

// get returns the breaker for one provider, creating it on first use.
//
// A plain mutex rather than sync.Map or an RWMutex: this runs once per request
// and holds the lock only for a map lookup, so contention is not the cost that
// matters next to an upstream LLM call.
func (g *breakerGroup) get(providerName string) *gobreaker.CircuitBreaker {
	g.mu.Lock()
	defer g.mu.Unlock()

	if b, ok := g.breakers[providerName]; ok {
		return b
	}
	b := newBreaker(providerName, g.cfg, g.metrics, g.sharer)
	g.breakers[providerName] = b
	return b
}

// newBreaker builds the circuit breaker wrapping one provider's upstream calls.
// When that provider is failing hard, the breaker OPENS and we fail fast with
// 503 instead of piling on more doomed requests — protecting both upstream and
// our own latency.
func newBreaker(
	providerName string, cfg Config, m *Metrics, sharer *BreakerSharer,
) *gobreaker.CircuitBreaker {
	return gobreaker.NewCircuitBreaker(gobreaker.Settings{
		Name:    providerName,
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
		// name is gobreaker's Name above, i.e. the provider. Taken from the
		// callback rather than the closure so the gauge cannot drift from the
		// breaker that actually changed state.
		OnStateChange: func(name string, _ gobreaker.State, to gobreaker.State) {
			// Surface breaker state as a gauge for dashboards/alerts, one series
			// per provider — an unlabelled gauge would let the last provider to
			// change state overwrite every other provider's reading.
			gauge := m.circuitState.WithLabelValues(name)
			switch to {
			case gobreaker.StateClosed:
				gauge.Set(0)
			case gobreaker.StateHalfOpen:
				gauge.Set(1)
			case gobreaker.StateOpen:
				gauge.Set(2)
				// Tell the other replicas, so they can shed on this replica's
				// evidence instead of each collecting CircuitMinReqs failures of
				// their own against an upstream already known to be down.
				//
				// Published only on OPEN, never cleared on CLOSED: the flag
				// carries its own TTL and recovery is governed by each replica's
				// own half-open probe. An explicit clear would let the first
				// replica to recover speak for all of them, before the others
				// have evidence the upstream is actually healthy.
				//
				// A fresh context, not the request's: gobreaker invokes this
				// callback synchronously from whichever request tripped the
				// breaker, and that request's context may already be cancelled —
				// which is exactly the case where publishing matters most.
				ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
				defer cancel()
				sharer.publishOpen(ctx, name)
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

		// Remember the most INFORMATIVE failure, not simply the most recent.
		//
		// An upstream error names what the vendor actually said ("quota
		// exceeded", 429); a transport error only says the call didn't finish.
		// Plain last-wins would let a dropped connection on the final attempt
		// erase a real 429 from an earlier one, and serveBuffered's errors.As
		// would then miss — downgrading a precise vendor error into a generic
		// 503 "upstream unavailable".
		if lastErr == nil || !isUpstreamErr(lastErr) || isUpstreamErr(err) {
			last, lastErr = res, err
		}

		// Don't sleep after the final attempt.
		if attempt == cfg.RetryMax-1 {
			break
		}

		delay := backoffDelay(cfg, attempt, seed)
		if res != nil {
			if ra := parseRetryAfter(res.header.Get("Retry-After")); ra > 0 {
				// Upstream told us exactly how long to wait — respect it, but
				// never unconditionally. A buggy or hostile provider sending
				// "Retry-After: 86400" would otherwise park this request for a
				// day, holding a connection and a singleflight slot. Cap it at
				// the same ceiling backoffDelay already honors.
				if ra > cfg.RetryMaxDly {
					ra = cfg.RetryMaxDly
				}
				delay = ra
			}
		}
		select {
		case <-ctx.Done():
			// Giving up mid-backoff must not throw away what we already
			// learned. Preserving the error through the loop (above) is
			// pointless if this exit replaces it with "context canceled".
			//
			// The two cancellation causes are NOT equivalent:
			//   - Canceled: the client hung up. Nobody is waiting for a
			//     response, and ctx.Err() is the honest description of why we
			//     stopped, so report that.
			//   - DeadlineExceeded: we ran out of time, but a caller IS still
			//     listening. The vendor's own 429/5xx is strictly better
			//     information than "deadline exceeded", and serveBuffered's
			//     errors.As needs the *UpstreamError to surface a real status.
			if !errors.Is(ctx.Err(), context.Canceled) && isUpstreamErr(lastErr) {
				return last, lastErr
			}
			return last, ctx.Err()
		case <-time.After(delay):
		}
	}
	// Every attempt failed — reaching here requires it, since a success returns
	// immediately — so lastErr is always set.
	return last, lastErr
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

// parseRetryAfter handles both Retry-After forms: delta-seconds ("120") and
// HTTP-date ("Wed, 21 Oct 2026 07:28:00 GMT"). Google emits the date form.
//
// Anything unusable returns <= 0, which the caller reads as "no hint" and
// falls back to normal backoff.
func parseRetryAfter(v string) time.Duration {
	v = strings.TrimSpace(v)

	// delta-seconds.
	if secs, err := strconv.Atoi(v); err == nil {
		if secs <= 0 {
			return 0
		}
		return time.Duration(secs) * time.Second
	}

	// HTTP-date, e.g. "Wed, 21 Oct 2026 07:28:00 GMT". A date already in the
	// past yields a negative duration, which the caller treats as "no hint".
	if when, err := http.ParseTime(v); err == nil {
		return time.Until(when)
	}
	return 0
}
