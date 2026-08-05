package mockupstream

import (
	"hash/fnv"
	"math/rand/v2"
	"net/http"
	"strconv"
	"sync"
	"time"
)

// seedFor derives this request's RNG seed.
//
// This is the mechanism that lets "inject jitter" and "be deterministic"
// coexist. The obvious implementation — one package-level RNG advanced per
// request — is reproducible only if requests arrive in exactly the same order,
// which stops being true the moment the benchmark opens a second connection.
// Seeding from the request itself removes the shared state entirely: a given
// request draws the same numbers no matter how many other requests are in
// flight, on any machine, in any run.
//
// nonce is the escape hatch. Identical requests otherwise share a verdict, so
// error_rate=0.5 against one repeated body would return all-fail or all-succeed
// rather than half. Load generators that set X-Request-Id (or X-Mock-Nonce) get
// a real distribution, while a bare curl stays perfectly reproducible.
func seedFor(cfg Config, method, path string, body []byte, nonce string) (uint64, uint64) {
	h := fnv.New64a()
	_, _ = h.Write([]byte(method))
	_, _ = h.Write([]byte{0})
	_, _ = h.Write([]byte(path))
	_, _ = h.Write([]byte{0})
	_, _ = h.Write(body)
	_, _ = h.Write([]byte{0})
	_, _ = h.Write([]byte(cfg.fingerprint()))
	_, _ = h.Write([]byte{0})
	_, _ = h.Write([]byte(nonce))

	lo := h.Sum64()
	// Mix into a second, decorrelated word: PCG wants two independent seeds, and
	// handing it the same value twice weakens the stream.
	hi := lo ^ 0x9e3779b97f4a7c15
	hi *= 0xbf58476d1ce4e5b9
	return lo, hi
}

// requestNonce returns the client-supplied value that opts a request out of
// sharing a verdict with its identical twins.
func requestNonce(r *http.Request) string {
	if v := r.Header.Get("X-Mock-Nonce"); v != "" {
		return v
	}
	return r.Header.Get("X-Request-Id")
}

// chaos is the per-request verdict: how long to stall, and whether to fail.
//
// It deliberately does NOT expose its generator. Response text draws from a
// separately seeded stream (see contentRNG), so that toggling jitter cannot
// shift the stream position and change the words in the reply.
type chaos struct {
	delay  time.Duration
	fail   bool
	status int
}

// decide computes the verdict for one request. It performs no I/O and does not
// sleep; the caller applies the delay so streaming handlers can split it across
// chunks.
func decide(cfg Config, r *http.Request, body []byte) chaos {
	lo, hi := seedFor(cfg, r.Method, r.URL.Path, body, requestNonce(r))
	rng := rand.New(rand.NewPCG(lo, hi))

	// Draw in a FIXED order — jitter, then failure — so adding a knob later
	// cannot silently reshuffle existing verdicts.
	//
	// CAVEAT: that only holds for knobs which draw UNCONDITIONALLY. The jitter
	// draw below is guarded on Jitter > 0, so turning jitter on consumes a number
	// and shifts the failure roll that follows — 21 of 40 nonces flip verdict at
	// an unchanged error_rate=0.5. The error-rate roll gets this right by always
	// drawing, even at ErrorRate >= 1.
	//
	// Left as-is: a fix changes every seeded value and invalidates any captured
	// baseline. The consequence for callers — hold Jitter fixed across arms of a
	// comparison, or the arms run against different failure sets — is documented
	// in README.md and pinned by TestJitterShiftsFailureVerdictQuirk.
	delay := cfg.Latency
	if cfg.Jitter > 0 {
		delay += time.Duration(rng.Int64N(int64(cfg.Jitter) + 1))
	}

	fail := false
	if cfg.ErrorRate > 0 {
		// Always draw, even at ErrorRate >= 1, to keep the stream position
		// independent of the configured rate.
		roll := rng.Float64()
		fail = roll < cfg.ErrorRate || cfg.ErrorRate >= 1
	}

	return chaos{delay: delay, fail: fail, status: cfg.ErrorStatus}
}

// sleep applies the injected latency, aborting early if the client hangs up.
func (c chaos) sleep(r *http.Request) bool {
	if c.delay <= 0 {
		return true
	}
	select {
	case <-time.After(c.delay):
		return true
	case <-r.Context().Done():
		return false
	}
}

// outage is a wall-clock window during which every request fails, regardless of
// path or error rate. It models a total provider outage.
//
// This is the one deliberate exception to the mock's determinism: it is stateful
// and time-based by definition. It is off unless a test switches it on, so the
// reproducibility guarantee holds for every request outside a window.
type outage struct {
	mu    sync.RWMutex
	until time.Time
}

// start opens an outage window of d from now. A non-positive d clears it.
func (o *outage) start(d time.Duration) time.Time {
	o.mu.Lock()
	defer o.mu.Unlock()
	if d <= 0 {
		o.until = time.Time{}
		return o.until
	}
	o.until = time.Now().Add(d)
	return o.until
}

// active reports whether a window is currently open.
func (o *outage) active() bool {
	o.mu.RLock()
	defer o.mu.RUnlock()
	return !o.until.IsZero() && time.Now().Before(o.until)
}

// deadline returns the end of the current window, or the zero time.
func (o *outage) deadline() time.Time {
	o.mu.RLock()
	defer o.mu.RUnlock()
	if o.until.IsZero() || !time.Now().Before(o.until) {
		return time.Time{}
	}
	return o.until
}

// writeFailure emits an injected failure in the provider's error envelope,
// choosing the OpenAI or Google shape so a native-API client is never handed a
// foreign-looking error.
//
// Retry-After is set before WriteHeader because headers written afterwards are
// silently dropped — the exact bug that would make the proxy's Retry-After
// handling look broken when it is the mock at fault.
func writeFailure(w http.ResponseWriter, status, retryAfter int, reason string, gemini bool) {
	if retryAfter > 0 {
		w.Header().Set("Retry-After", strconv.Itoa(retryAfter))
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Mock-Injected", reason)
	w.WriteHeader(status)
	if gemini {
		_, _ = w.Write(geminiErrorEnvelope(status, reason))
		return
	}
	_, _ = w.Write(errorEnvelope(status, reason))
}
