package gateway

// Characterization: the circuit breaker.
// Harness and thresholds live in harness_test.go.

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"documedai/llmguard/internal/testutil"
	"documedai/llmguard/mockupstream"
	"documedai/llmguard/provider"
	"documedai/llmguard/provider/vertex"
)

// Pins CircuitMinReqs=10 and CircuitFailRatio=0.6. The breaker wraps the WHOLE
// retry loop, so one client request is ONE breaker observation regardless of how
// many upstream attempts it burns.
func TestCircuitBreakerTrips(t *testing.T) {
	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond // timing only
	cfg.RetryMaxDly = 5 * time.Millisecond

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusInternalServerError
	h := newHarness(t, cfg, mcfg, nil)

	body := chatBody("gemini-2.5-flash", "sustained failure", false)

	// Sequential, so each request is its own breaker observation rather than
	// several overlapping in one window.
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
	if got := testutil.LabeledGaugeValue(t, h.metrics.circuitState, "mock", "gemini-2.5-flash"); got != 2 {
		t.Errorf("circuitState gauge = %v, want 2 (open)", got)
	}
}

// The breaker RECOVERS: open → half-open after CircuitOpenFor → closed once a
// probe succeeds, and real traffic flows again.
//
// Previously only the trip and the fail-fast were pinned; CircuitOpenFor was
// asserted nowhere. A breaker that opens and never closes is an outage, not a
// protection, and it fails in exactly the same way as a working one for the
// first 20 seconds — so the untested leg is the one that hides the worse bug.
//
// CircuitOpenFor is shortened here. That is a TIMING knob, the same category as
// the already-shortened RetryBaseDly/RetryMaxDly: it changes how long the
// breaker stays open, never what opens it or what closes it. The guard below
// keeps realDefaults honest so shortening it here cannot hide a change to the
// production default.
func TestCircuitBreakerRecovers(t *testing.T) {
	if got := realDefaults().CircuitOpenFor; got != 20*time.Second {
		t.Fatalf("realDefaults().CircuitOpenFor = %v, want 20s — this test shortens the "+
			"knob deliberately, so it must also assert what production actually uses", got)
	}

	const openFor = 200 * time.Millisecond

	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond // timing only
	cfg.RetryMaxDly = 5 * time.Millisecond
	cfg.CircuitOpenFor = openFor

	// healthy flips the upstream from "always fails" to "always works" WITHOUT
	// rebuilding the harness. mockupstream.Config is captured at New time, so a
	// second harness would mean a second breaker — and one breaker instance
	// living through both regimes is the entire point of a recovery test.
	var healthy atomic.Bool
	failing := mockupstream.New(func() mockupstream.Config {
		c := mockupstream.DefaultConfig()
		c.ErrorRate = 1.0
		c.ErrorStatus = http.StatusInternalServerError
		return c
	}())
	h := newHarnessWithHandler(t, cfg, mockupstream.DefaultConfig(), nil,
		func(mock http.Handler, w http.ResponseWriter, r *http.Request) {
			if healthy.Load() {
				mock.ServeHTTP(w, r) // the harness's own mock: default config, no errors
				return
			}
			failing.ServeHTTP(w, r)
		})

	body := chatBody("gemini-2.5-flash", "sustained failure", false)

	// --- Trip it, exactly as TestCircuitBreakerTrips does ---
	for i := 1; i <= 10; i++ {
		if rec := h.do(t, body, nil); rec.Code != http.StatusInternalServerError {
			t.Fatalf("request %d: status = %d, want 500 while the breaker is still closed",
				i, rec.Code)
		}
	}
	if rec := h.do(t, body, nil); rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("status after trip = %d, want 503 — the breaker must be open before "+
			"recovery means anything", rec.Code)
	}
	if got := testutil.LabeledGaugeValue(t, h.metrics.circuitState, "mock", "gemini-2.5-flash"); got != 2 {
		t.Fatalf("circuitState gauge = %v, want 2 (open)", got)
	}

	hitsWhileOpen := h.up.Hits()

	// --- Heal the upstream and wait out CircuitOpenFor ---
	healthy.Store(true)

	// gobreaker moves to half-open lazily, on the first request after the timeout
	// expires, not on a timer — so the gauge cannot change until traffic arrives.
	// Poll by SENDING requests: each is both the probe and the observation.
	//
	// A generous timeout against a 200ms window, since the assertion is that
	// recovery happens at all, not that it happens fast.
	var recovered *httptest.ResponseRecorder
	testutil.RequireEventually(t, 5*time.Second, openFor/4, func() bool {
		rec := h.do(t, body, nil)
		if rec.Code == http.StatusOK {
			recovered = rec
			return true
		}
		return false
	}, "the breaker never closed after CircuitOpenFor elapsed with a healthy upstream")

	// --- The gauge alone does not prove traffic flows; check the response ---
	if got := testutil.LabeledGaugeValue(t, h.metrics.circuitState, "mock", "gemini-2.5-flash"); got != 0 {
		t.Errorf("circuitState gauge = %v, want 0 (closed) after a successful probe", got)
	}
	if got := decodeChat(t, recovered); len(got.Choices) == 0 || got.Choices[0].Message.Content == "" {
		t.Errorf("recovered response carries no content: %+v", got)
	}
	if got := h.up.Hits(); got <= hitsWhileOpen {
		t.Errorf("upstream hits = %d, want > %d — a closed breaker must dispatch again",
			got, hitsWhileOpen)
	}

	// And it stays closed: the next request is served without another probe cycle.
	if rec := h.do(t, body, nil); rec.Code != http.StatusOK {
		t.Errorf("status = %d, want 200 — the breaker must stay closed once recovered", rec.Code)
	}
}

// Client errors must NOT trip the breaker. The breaker's job is to detect a
// sick provider; a 400 means the CALLER sent something bad, and shedding
// everyone else's traffic because one client is misbehaving is a self-inflicted
// outage.
//
// Without gobreaker's IsSuccessful set, its default counts every non-nil error
// as a failure — so this many 400s would have opened the breaker and started
// answering unrelated requests with 503.
func TestCircuitBreakerIgnoresClientErrors(t *testing.T) {
	const requests = 15 // comfortably past CircuitMinReqs=10

	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond // timing only
	cfg.RetryMaxDly = 5 * time.Millisecond

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusBadRequest
	h := newHarness(t, cfg, mcfg, nil)

	// Distinct bodies so each request is its own upstream call, and so
	// every one is a separate breaker observation.
	for i := 1; i <= requests; i++ {
		rec := h.do(t, chatBody("gemini-2.5-flash", fmt.Sprintf("bad request %d", i), false), nil)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("request %d: status = %d, want 400 — the breaker must stay closed "+
				"and keep surfacing the vendor's own error", i, rec.Code)
		}
	}

	// Closed throughout: 0 is the gauge's initial value and its closed value,
	// so the load-bearing assertion is that it never became 2 (open).
	if got := testutil.LabeledGaugeValue(t, h.metrics.circuitState, "mock", "gemini-2.5-flash"); got == 2 {
		t.Errorf("circuitState gauge = %v — %d client errors must not open the breaker",
			got, requests)
	}

	// Every request reached upstream exactly once: not retried (commit 1) and
	// never shed by an open breaker.
	if got := h.up.Hits(); got != requests {
		t.Errorf("upstream hits = %d, want %d (one attempt each, none shed)", got, requests)
	}

	// A subsequent request still gets through rather than a breaker-open 503.
	rec := h.do(t, chatBody("gemini-2.5-flash", "still open", false), nil)
	if rec.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400 — the breaker should still be closed", rec.Code)
	}
	if env := decodeError(t, rec); env.Error.Message == "upstream unavailable" {
		t.Error("got the breaker-open message; the breaker tripped on client errors")
	}
}

// healthyProvider is a second adapter, pointed at its own upstream, so one
// provider can fail while another stays up.
type healthyProvider struct{ *mockProvider }

func (p *healthyProvider) Name() string { return "healthy" }

// TestCircuitBreakerIsolatesProviders is why breakers are per provider: an
// upstream that fails hard must not shed traffic bound for a healthy one.
//
// With a single shared breaker this is exactly backwards — routing a model to a
// second provider would make an outage WORSE, since the failing upstream would
// take the working one down with it.
func TestCircuitBreakerIsolatesProviders(t *testing.T) {
	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond // timing only
	cfg.RetryMaxDly = 5 * time.Millisecond

	// The harness's own upstream fails every call; "mock" is routed to it.
	failing := mockupstream.DefaultConfig()
	failing.ErrorRate = 1.0
	failing.ErrorStatus = http.StatusInternalServerError
	h := newHarness(t, cfg, failing, nil)

	// A second upstream that answers normally, behind a second adapter.
	healthy := newMockUpstream(t, mockupstream.DefaultConfig())
	provider.Register(&healthyProvider{mockProvider: &mockProvider{
		base: healthy.server.URL, inner: &vertex.Client{},
	}})
	provider.SetRoutes([]provider.Route{
		{Provider: "mock", Model: "gemini-2.5-flash"},
		{Provider: "healthy", Model: "gemini-2.5-flash"},
	})

	// Trip the failing provider's breaker: 10 observations at ratio 1.0.
	failBody := chatBody("gemini-2.5-flash", "sustained failure", false)
	for i := 1; i <= 10; i++ {
		if rec := h.do(t, failBody, nil); rec.Code != http.StatusInternalServerError {
			t.Fatalf("request %d: status = %d, want 500", i, rec.Code)
		}
	}
	if rec := h.do(t, failBody, nil); rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503 — the failing provider's breaker must be open", rec.Code)
	}

	// The healthy provider is untouched by the other's outage.
	okBody := `{"provider":"healthy","model":"gemini-2.5-flash",` +
		`"messages":[{"role":"user","content":"still fine"}]}`
	if rec := h.do(t, okBody, nil); rec.Code != http.StatusOK {
		t.Errorf("healthy provider status = %d, want 200 — one provider's open breaker "+
			"must not shed another's traffic\nbody: %s", rec.Code, rec.Body.String())
	}
	if got := healthy.Hits(); got != 1 {
		t.Errorf("healthy upstream hits = %d, want 1 — its request must actually dispatch", got)
	}

	// And the gauges disagree, which an unlabelled gauge could not express.
	if got := testutil.LabeledGaugeValue(t, h.metrics.circuitState, "mock", "gemini-2.5-flash"); got != 2 {
		t.Errorf("circuitState{provider=\"mock\"} = %v, want 2 (open)", got)
	}
	if got := testutil.LabeledGaugeValue(t, h.metrics.circuitState, "healthy", "gemini-2.5-flash"); got != 0 {
		t.Errorf("circuitState{provider=\"healthy\"} = %v, want 0 (closed)", got)
	}
}

// Sustained upstream 429s do NOT open the breaker.
//
// Contract test: the trip rule used to be isRetryable, so 429 both retried and
// tripped. Quota exhaustion is not ill health — opening on it fails fast for
// CircuitOpenFor, then finds the quota still spent, so the breaker cannot recover
// on its own evidence. Retry still applies; only the trip rule changed.
func TestCircuitBreakerIgnoresUpstreamRateLimits(t *testing.T) {
	const requests = 15 // past CircuitMinReqs=10

	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond // timing only
	cfg.RetryMaxDly = 5 * time.Millisecond

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusTooManyRequests
	h := newHarness(t, cfg, mcfg, nil)

	for i := 1; i <= requests; i++ {
		rec := h.do(t, chatBody("gemini-2.5-flash", fmt.Sprintf("quota %d", i), false), nil)
		// The vendor's own 429 is surfaced, never converted to the breaker's 503.
		if rec.Code != http.StatusTooManyRequests {
			t.Fatalf("request %d: status = %d, want 429", i, rec.Code)
		}
	}

	if got := testutil.LabeledGaugeValue(
		t, h.metrics.circuitState, "mock", "gemini-2.5-flash"); got == 2 {
		t.Errorf("circuitState = %v — %d upstream 429s must not open the breaker",
			got, requests)
	}
}

// One route's outage does not shed another route on the SAME provider.
//
// Contract test: the breaker was keyed on the provider, so a single sick model
// took every model on that upstream with it. Both routes here share provider
// "mock" and differ only by model, which is the case a per-provider key cannot
// express.
func TestCircuitBreakerIsolatesRoutesOnOneProvider(t *testing.T) {
	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond
	cfg.RetryMaxDly = 5 * time.Millisecond

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusInternalServerError
	h := newHarness(t, cfg, mcfg, nil)

	// A second route on the same provider. The upstream fails for both; only the
	// first one's breaker is driven to trip.
	provider.SetRoutes([]provider.Route{
		{Provider: "mock", Model: "gemini-2.5-flash"},
		{Provider: "mock", Model: "gemini-2.5-pro"},
	})

	for i := 1; i <= 10; i++ {
		if rec := h.do(t, chatBody("gemini-2.5-flash", fmt.Sprintf("sick %d", i), false), nil); rec.Code != http.StatusInternalServerError {
			t.Fatalf("request %d: status = %d, want 500", i, rec.Code)
		}
	}
	if rec := h.do(t, chatBody("gemini-2.5-flash", "one more", false), nil); rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503 — the flash route's breaker must be open", rec.Code)
	}

	// The other model on the same upstream still dispatches: it reaches the
	// upstream and surfaces its 500, rather than being shed with the 503 above.
	if rec := h.do(t, chatBody("gemini-2.5-pro", "untouched", false), nil); rec.Code != http.StatusInternalServerError {
		t.Errorf("pro route status = %d, want 500 — one model's open breaker must not "+
			"shed another model on the same provider", rec.Code)
	}

	if got := testutil.LabeledGaugeValue(
		t, h.metrics.circuitState, "mock", "gemini-2.5-flash"); got != 2 {
		t.Errorf("circuitState{model=\"gemini-2.5-flash\"} = %v, want 2 (open)", got)
	}
	if got := testutil.LabeledGaugeValue(
		t, h.metrics.circuitState, "mock", "gemini-2.5-pro"); got == 2 {
		t.Errorf("circuitState{model=\"gemini-2.5-pro\"} = %v, want != 2", got)
	}
}

// TestCircuitBreakerTripsAfterALongHealthyHistory pins the fix for a breaker that
// stopped being able to open.
//
// gobreaker's zero Interval means "never reset the counts while closed", which
// makes the failure ratio a LIFETIME average rather than a current reading. A
// replica that has served thousands of requests carries a denominator no burst
// of failures can move: measured on the multi-replica stack, an 80% error rate
// left the breaker closed indefinitely after ~17k healthy requests, while the
// same load tripped it in 15 requests on a freshly started replica -- longer
// uptime meant less protection.
//
// The healthy prelude is what makes this a regression test: drop it and the
// assertion passes with Interval unset.
func TestCircuitBreakerTripsAfterALongHealthyHistory(t *testing.T) {
	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond // timing only
	cfg.RetryMaxDly = 5 * time.Millisecond
	// Shortened from the production minute so the test crosses a window boundary
	// in seconds. It cannot go much lower: a window must be long enough to hold
	// CircuitMinReqs observations, or every request lands in a fresh generation
	// and Requests never reaches the minimum -- at 100ms this test failed for
	// that reason, not for the one it exists to catch.
	cfg.CircuitInterval = 2 * time.Second

	// failing flips once the healthy prelude is done. The mock's config is fixed
	// at construction, so the switch lives in the intercept instead.
	var failing atomic.Bool
	h := newHarnessWithHandler(t, cfg, mockupstream.DefaultConfig(), nil,
		func(mock http.Handler, w http.ResponseWriter, r *http.Request) {
			if failing.Load() {
				w.WriteHeader(http.StatusServiceUnavailable)
				return
			}
			mock.ServeHTTP(w, r)
		})

	// Long enough that a lifetime ratio could never reach 0.6 again: 200
	// successes would need 300 failures, far past the 10 sent below.
	for i := 0; i < 200; i++ {
		if rec := h.do(t, chatBody("gemini-2.5-flash", fmt.Sprintf("healthy %d", i), false), nil); rec.Code != http.StatusOK {
			t.Fatalf("healthy prelude %d: status = %d, want 200", i, rec.Code)
		}
	}

	failing.Store(true)
	// Let the healthy window close, which is what Interval buys: the counts the
	// prelude accumulated are dropped rather than averaged against forever.
	time.Sleep(2100 * time.Millisecond)
	// CircuitMinReqs=10 observations at a 1.0 failure ratio is exactly the trip
	// condition, measured over CircuitInterval rather than over all time.
	for i := 0; i < 10; i++ {
		h.do(t, chatBody("gemini-2.5-flash", fmt.Sprintf("failing %d", i), false), nil)
	}

	if got := testutil.LabeledGaugeValue(t, h.metrics.circuitState, modelLabels("gemini-2.5-flash")...); got != 2 {
		t.Errorf("circuit_state = %v, want 2 (open) -- a healthy history is diluting the failure ratio", got)
	}
}
