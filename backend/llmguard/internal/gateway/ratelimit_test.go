package gateway

// Characterization: rate-limit shedding.
// Harness and thresholds live in harness_test.go.

import (
	"context"
	"net/http"
	"sync"
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
	if got := testutil.LabeledCounterValue(t, h.metrics.rateLimited, modelLabels("gemini-2.5-flash")...); got != 1 {
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

// Two replicas sharing one Redis enforce ONE aggregate limit.
//
// This is the property that makes the limiter horizontally scalable: adding a
// replica must not raise the effective rate. The bucket lives in Redis and the
// key is derived entirely from request identity — apiKeyHint(r) + ":" + model in
// proxy.go, with nothing replica-local in it — so two processes serving the same
// caller and model address the same hash. This test pins that end to end rather
// than trusting it: if either limiter kept state of its own, or derived a
// different key, every caller would find a full bucket and `allowed` would come
// back at 2*burst.
//
// Needs a real Redis for the same reason TestRateLimitShedding does: with Redis
// unreachable Acquire fails OPEN and nothing is ever refused, so the behavior
// under test cannot occur offline. Skips when absent; CI provides one.
func TestRateLimitSharedAcrossReplicas(t *testing.T) {
	// Two clients, not one shared: each carries its own connection pool, which is
	// the thing that actually differs between two processes. One client would
	// still exercise the Lua script, but it would also be the only place any
	// per-connection state could hide.
	rdbA := testutil.RequireRedis(t)
	rdbB := testutil.RequireRedis(t)

	const (
		// 6 RPM = 0.1 tokens/s, deliberately far below production's 480.
		//
		// The clock is wall time read inside take() and is not injectable, so the
		// bucket refills WHILE the test runs. Choosing a rate this low makes that
		// refill irrelevant rather than racing it: firing every caller takes
		// milliseconds, over which 0.1/s produces ~0.001 tokens — three orders of
		// magnitude below the `tokens >= 1` the script needs to grant. That is what
		// makes `allowed == burst` an exact assertion instead of a flaky one.
		rpm   = 6
		burst = 20

		// Fail fast. Acquire POLLS until maxWait elapses, so at the production 5s
		// every refused caller would simply wait for a refilled token and the test
		// would measure nothing. 0 gives each caller exactly one attempt.
		maxWait = 0
	)

	// Both limiters MUST pass this same string — one shared bucket is the entire
	// point. t.Name() keeps it unique per run so nothing already in DB 15 can
	// pollute the count.
	key := "shared-replica-test:" + t.Name()

	replicaA := newRateLimiter(rdbA, rpm, burst)
	replicaB := newRateLimiter(rdbB, rpm, burst)

	// Twice the burst, so the bucket is guaranteed to run dry and the refused half
	// is not an artifact of offering too little load.
	const callers = burst * 2

	// One slot per goroutine. Each writes only its own index, so there is no
	// shared counter to race on and this stays clean under -race.
	granted := make([]bool, callers)
	servedBy := make([]string, callers)

	var wg sync.WaitGroup
	// Lines every goroutine up before any of them calls Acquire. Without the
	// handshake the first few drain the bucket before the rest have started, and
	// the test degenerates into a sequential drain — which would pass even if the
	// two limiters were using entirely separate buckets.
	start := make(chan struct{})

	for i := range callers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			// Alternate replicas, the way a round-robin load balancer would.
			limiter, name := replicaA, "A"
			if i%2 == 1 {
				limiter, name = replicaB, "B"
			}
			servedBy[i] = name
			<-start
			granted[i] = limiter.Acquire(context.Background(), key, maxWait)
		}()
	}
	close(start)
	wg.Wait()

	allowed, rejected := 0, 0
	perReplica := map[string]int{}
	for i, ok := range granted {
		if ok {
			allowed++
			perReplica[servedBy[i]]++
			continue
		}
		rejected++
	}

	// The headline: one bucket, not two. A split of the grants between A and B is
	// not asserted — either replica may legitimately win the race for all of them
	// — but the TOTAL is what proves they shared state.
	if allowed != burst {
		t.Errorf("allowed = %d, want %d — two replicas sharing one Redis must not "+
			"raise the aggregate limit (replica A granted %d, replica B granted %d)",
			allowed, burst, perReplica["A"], perReplica["B"])
	}

	// Liveness tripwire, and the reason the assertion above cannot pass vacuously.
	// Acquire fails OPEN on any Redis error, so a server that died mid-test would
	// grant every caller and leave this at 0. Requiring a refusal proves the
	// limiter was actually enforcing rather than waving everything through.
	if rejected < 1 {
		t.Errorf("rejected = %d, want >= 1 — nothing was refused, so the limiter was "+
			"not enforcing (a Redis error makes Acquire fail open and grant everyone)",
			rejected)
	}
}
