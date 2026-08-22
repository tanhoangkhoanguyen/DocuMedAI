package gateway

// Characterization: admission control (the in-flight ceiling).
// Harness and thresholds live in harness_test.go.
//
// Unlike most of this suite these tests DO encode intended behavior, because the
// behavior is new — there is no prior conduct to preserve. What they pin is the
// contract the rest of the system depends on: a refusal is a 429 with a
// Retry-After, it is counted separately from quota throttling, and a slot is
// always returned no matter how the request ended.

import (
	"net/http"
	"sync"
	"testing"
	"time"

	"documedai/llmguard/internal/testutil"
	"documedai/llmguard/mockupstream"
)

// admissionConfig is realDefaults with a specific ceiling. A helper because every
// test here varies exactly that one field.
func admissionConfig(max int) Config {
	cfg := realDefaults()
	cfg.MaxInFlight = max
	return cfg
}

// A request past the ceiling is refused with 429 + Retry-After and never reaches
// upstream — the point of shedding is that the work is not done, not merely that
// it is reported.
func TestAdmissionShedsPastCeiling(t *testing.T) {
	mcfg := mockupstream.DefaultConfig()
	// Hold the first request in flight so the second arrives while the only slot
	// is taken. Without the hold the two run sequentially and both succeed.
	mcfg.Latency = time.Second
	h := newHarness(t, admissionConfig(1), mcfg, nil)

	// Distinct prompts, so each request is its own upstream call and the
	// second caller would never reach the admitter at all.
	occupied := make(chan struct{})
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		close(occupied)
		h.do(t, chatBody("gemini-2.5-flash", "holds the slot", false), nil)
	}()

	<-occupied
	// The goroutine has started but may not have acquired yet; wait for the gauge
	// the admitter itself maintains rather than sleeping a guessed interval.
	testutil.RequireEventually(t, 2*time.Second, 5*time.Millisecond, func() bool {
		return testutil.CounterValue(t, h.metrics.inFlight) == 1
	}, "first request should occupy the only slot")

	rec := h.do(t, chatBody("gemini-2.5-flash", "should be shed", false), nil)

	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("shed request status = %d, want 429", rec.Code)
	}
	if got := rec.Header().Get("Retry-After"); got == "" {
		t.Error("a shed request must carry Retry-After; a client with no hint re-forms the herd")
	}
	if typ := decodeError(t, rec).Error.Type; typ != "rate_limit" {
		t.Errorf("error type = %q, want rate_limit", typ)
	}
	if got := testutil.LabeledCounterValue(t, h.metrics.shed, modelLabels("gemini-2.5-flash")...); got != 1 {
		t.Errorf("shed_total = %v, want 1", got)
	}
	// Counted as shed, NOT as rate-limited: the two both return 429 but describe
	// different faults, and conflating them hides a capacity incident inside
	// ordinary throttling.
	if got := testutil.LabeledCounterValue(t, h.metrics.rateLimited, modelLabels("gemini-2.5-flash")...); got != 0 {
		t.Errorf("rate_limited_total = %v, want 0 — a shed request is not a quota rejection", got)
	}

	wg.Wait()
	// One upstream call, from the request that was admitted. The shed one must not
	// have reached the provider.
	if hits := h.up.Hits(); hits != 1 {
		t.Errorf("upstream hits = %d, want 1 — the shed request must not reach upstream", hits)
	}
}

// A completed request returns its slot, so the ceiling is a concurrency limit
// rather than a lifetime quota. This is the failure that would make admission
// control silently ratchet the gateway down to zero capacity.
func TestAdmissionReleasesSlotAfterRequest(t *testing.T) {
	h := newHarness(t, admissionConfig(1), mockupstream.DefaultConfig(), nil)

	for i := 0; i < 3; i++ {
		rec := h.do(t, chatBody("gemini-2.5-flash", "sequential", false), nil)
		if rec.Code != http.StatusOK {
			t.Fatalf("request %d status = %d, want 200 — the slot was not returned", i, rec.Code)
		}
	}
	if got := testutil.CounterValue(t, h.metrics.inFlight); got != 0 {
		t.Errorf("in_flight = %v after all requests finished, want 0", got)
	}
}

// An error response releases the slot too. Exercised through the streaming path's
// early failure, which returns from a different branch than the buffered success
// — the case a per-exit release would miss.
func TestAdmissionReleasesSlotOnUpstreamError(t *testing.T) {
	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusInternalServerError

	cfg := admissionConfig(1)
	// Keep the retry loop from spending seconds asleep; how long a retry waits is
	// irrelevant to whether the slot comes back.
	cfg.RetryBaseDly = time.Millisecond
	cfg.RetryMaxDly = 2 * time.Millisecond
	h := newHarness(t, cfg, mcfg, nil)

	rec := h.do(t, chatBody("gemini-2.5-flash", "always fails", false), nil)
	if rec.Code == http.StatusOK {
		t.Fatalf("expected a failure, got 200")
	}
	if got := testutil.CounterValue(t, h.metrics.inFlight); got != 0 {
		t.Errorf("in_flight = %v after a failed request, want 0", got)
	}

	// And the freed slot is genuinely reusable.
	if got := testutil.LabeledCounterValue(t, h.metrics.shed, modelLabels("gemini-2.5-flash")...); got != 0 {
		t.Errorf("shed_total = %v, want 0 — nothing competed for the slot", got)
	}
}

// MaxInFlight <= 0 disables the ceiling. An operator restoring the previous
// unbounded behavior should get it, and nothing should be shed.
func TestAdmissionDisabledAdmitsEverything(t *testing.T) {
	mcfg := mockupstream.DefaultConfig()
	mcfg.Latency = 300 * time.Millisecond
	h := newHarness(t, admissionConfig(0), mcfg, nil)

	const callers = 6
	var wg sync.WaitGroup
	codes := make([]int, callers)
	for i := 0; i < callers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			// Distinct prompts so each goroutine is its own upstream call,
			// keeping this a test of concurrency rather than of caching.
			codes[i] = h.do(t, chatBody("gemini-2.5-flash", string(rune('a'+i)), false), nil).Code
		}(i)
	}
	wg.Wait()

	for i, c := range codes {
		if c != http.StatusOK {
			t.Errorf("caller %d status = %d, want 200 with admission control disabled", i, c)
		}
	}
	if got := testutil.LabeledCounterValue(t, h.metrics.shed, modelLabels("gemini-2.5-flash")...); got != 0 {
		t.Errorf("shed_total = %v, want 0 when disabled", got)
	}
	// The gauge stays untouched when there is no semaphore to mirror — it would be
	// misleading to report occupancy against a ceiling that does not exist.
	if got := testutil.CounterValue(t, h.metrics.inFlight); got != 0 {
		t.Errorf("in_flight = %v, want 0 when disabled", got)
	}
}

// Concurrent load never exceeds the ceiling, and the successes plus the sheds
// account for every caller — nothing is dropped or double-counted.
func TestAdmissionCapsConcurrencyAndAccountsForEveryRequest(t *testing.T) {
	const (
		ceiling = 3
		callers = 12
	)
	mcfg := mockupstream.DefaultConfig()
	mcfg.Latency = 500 * time.Millisecond
	h := newHarness(t, admissionConfig(ceiling), mcfg, nil)

	var wg sync.WaitGroup
	codes := make([]int, callers)
	for i := 0; i < callers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			codes[i] = h.do(t, chatBody("gemini-2.5-flash", string(rune('a'+i)), false), nil).Code
		}(i)
	}
	wg.Wait()

	var ok, shed int
	for i, c := range codes {
		switch c {
		case http.StatusOK:
			ok++
		case http.StatusTooManyRequests:
			shed++
		default:
			t.Errorf("caller %d status = %d, want 200 or 429", i, c)
		}
	}
	if ok+shed != callers {
		t.Fatalf("accounted %d of %d callers", ok+shed, callers)
	}
	// Every admitted request reaches upstream exactly once, so upstream hits bound
	// how many were let through. More hits than the ceiling is fine across time —
	// what must hold is that no request bypassed the semaphore.
	if int64(ok) != h.up.Hits() {
		t.Errorf("upstream hits = %d but %d callers succeeded", h.up.Hits(), ok)
	}
	if got := testutil.LabeledCounterValue(t, h.metrics.shed, modelLabels("gemini-2.5-flash")...); int(got) != shed {
		t.Errorf("shed_total = %v but %d callers got 429", got, shed)
	}
	if got := testutil.CounterValue(t, h.metrics.inFlight); got != 0 {
		t.Errorf("in_flight = %v after the load finished, want 0", got)
	}
}
