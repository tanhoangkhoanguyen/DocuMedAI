package gateway

// Admission control: a hard ceiling on how many requests may be in flight at
// once, and a clean refusal for everything past it.
//
// This is the one protection LLMGuard did not have. Retry, the circuit breaker
// and dedup all protect the UPSTREAM from us; nothing protected the gateway from
// its own callers. The rate limiter looks like it should, but it bounds the
// arrival RATE (requests per minute) — not the number running concurrently, and
// those diverge exactly when it matters. At the default 480 RPM with LLM calls
// taking 2-30s, roughly 160 requests are legitimately in flight at any moment;
// if upstream slows to 60s, the same admitted rate produces ~480, each holding a
// goroutine, a response buffer of up to maxUpstreamBody, and a pooled upstream
// connection. Arrival rate never signals that, so the limiter keeps admitting
// while memory runs out.
//
// A gateway whose whole claim is reliable LLM calls must not become the new
// failure. Refusing some requests is strictly better than an OOM that fails
// every request, including the ones already half-served.

// admitter is a counting semaphore over in-flight requests.
//
// A buffered channel rather than sync.Cond or a counter+mutex: a send is the
// acquire, a receive is the release, and `select` with a default gives the
// non-blocking attempt for free — no lock to hold across a request that may run
// for a minute.
type admitter struct {
	// slots is nil when admission control is disabled, which makes tryAcquire a
	// nil check rather than a branch on a separate bool that could disagree with
	// it.
	slots   chan struct{}
	metrics *Metrics
}

// newAdmitter builds the semaphore. max <= 0 disables admission control
// entirely: an operator who wants the pre-existing unbounded behavior can ask
// for it explicitly, and that reads better in config than a sentinel.
func newAdmitter(max int, m *Metrics) *admitter {
	a := &admitter{metrics: m}
	if max > 0 {
		a.slots = make(chan struct{}, max)
	}
	return a
}

// tryAcquire takes a slot without waiting, returning the release func and
// whether a slot was granted.
//
// Deliberately non-blocking. Queueing here would be a second rate limiter, and a
// worse one: a request parked waiting for a slot still owns a goroutine and its
// buffers, so the queue grows the very resource the semaphore exists to bound,
// while adding latency to the caller that is already being told "too busy". Fast
// refusal hands the decision back to the client, which can retry, fail over, or
// shed its own work — none of which it can do while blocked on us.
//
// The returned release is safe to call exactly once and is a no-op when
// admission control is disabled, so callers can `defer` it unconditionally.
func (a *admitter) tryAcquire() (release func(), ok bool) {
	if a.slots == nil {
		return func() {}, true
	}
	select {
	case a.slots <- struct{}{}:
		// The gauge is updated here rather than by the caller so it cannot drift
		// from the semaphore it describes — the same reason newBreaker's
		// OnStateChange owns circuitState.
		a.metrics.inFlight.Inc()
		return a.release, true
	default:
		return nil, false
	}
}

// release returns one slot. Split out as a method so tryAcquire hands back a
// value that needs no closure over per-request state.
func (a *admitter) release() {
	<-a.slots
	a.metrics.inFlight.Dec()
}
