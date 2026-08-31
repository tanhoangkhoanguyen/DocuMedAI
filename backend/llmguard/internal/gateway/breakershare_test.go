package gateway

// Cross-replica circuit-breaker propagation.
//
// These need a live Redis and SKIP without one, following the rate-limiter tests:
// `make test` has to stay green on a laptop, and CI is where Redis is guaranteed.
//
// "Replica" here is a second BreakerSharer over the same Redis DB. That is the
// whole of what separates two replicas for this feature — they share the flag and
// nothing else — so a second process would add startup cost without adding
// coverage.

import (
	"context"
	"testing"
	"time"

	"documedai/llmguard/internal/testutil"
	"documedai/llmguard/provider"
)

// The sharer keys on the route, so these stand in for the provider names the
// tests used before.
var (
	rtVertex = provider.Route{Provider: "vertex", Model: "m"}
	rtOther  = provider.Route{Provider: "openrouter", Model: "m"}
)

func TestBreakerSharerPropagatesOpenToAnotherReplica(t *testing.T) {
	rdb := testutil.RequireRedis(t)
	ctx := context.Background()

	replicaA := newBreakerSharer(rdb, 20*time.Second)
	replicaB := newBreakerSharer(rdb, 20*time.Second)

	if replicaB.isOpenElsewhere(ctx, rtVertex) {
		t.Fatal("no trip published yet, but replica B reports the provider open")
	}

	replicaA.publishOpen(ctx, rtVertex)

	if !replicaB.isOpenElsewhere(ctx, rtVertex) {
		t.Error("replica A tripped but replica B did not see it — each replica would " +
			"have to collect its own CircuitMinReqs failures against a dead upstream")
	}
	// Isolation still holds across providers: one sick upstream must not shed
	// traffic bound for a healthy one, which is the same property the per-provider
	// local breakers exist for.
	if replicaB.isOpenElsewhere(ctx, rtOther) {
		t.Error("a trip on vertex must not report openrouter as open")
	}
}

// The flag expires on its own. A replica that crashes while holding it open must
// not leave the others shedding forever.
func TestBreakerSharerFlagExpires(t *testing.T) {
	rdb := testutil.RequireRedis(t)
	ctx := context.Background()

	// A short TTL so the test does not wait out a production CircuitOpenFor. TTL
	// is what expiry is derived from, so shortening it tests the same mechanism.
	publisher := newBreakerSharer(rdb, 150*time.Millisecond)
	publisher.publishOpen(ctx, rtVertex)

	// A fresh reader per observation: the cache is per-sharer and would otherwise
	// answer from memory rather than from Redis, which is the thing under test.
	if !newBreakerSharer(rdb, 150*time.Millisecond).isOpenElsewhere(ctx, rtVertex) {
		t.Fatal("flag should be set immediately after publish")
	}

	testutil.RequireEventually(t, 2*time.Second, 20*time.Millisecond, func() bool {
		return !newBreakerSharer(rdb, 150*time.Millisecond).isOpenElsewhere(ctx, rtVertex)
	}, "the open flag must expire so a crashed replica cannot shed traffic forever")
}

// Only the POSITIVE reading is cached, and this is the asymmetry that makes the
// feature work at all.
//
// A cached negative would delay a replica's entry into an outage by a whole trust
// window, which is exactly the lateness the shared flag removes — so "healthy" is
// re-read every time while "down" is remembered. The first read below returns
// false; the read immediately after a publish must still see it.
func TestBreakerSharerDoesNotCacheHealthyReadings(t *testing.T) {
	rdb := testutil.RequireRedis(t)
	ctx := context.Background()

	// A long TTL means a long trust window, so a cached negative — if there were
	// one — would certainly still be in effect for the second read.
	sharer := newBreakerSharer(rdb, time.Minute)

	if sharer.isOpenElsewhere(ctx, rtVertex) {
		t.Fatal("nothing published yet, want not-open")
	}

	sharer.publishOpen(ctx, rtVertex)

	if !sharer.isOpenElsewhere(ctx, rtVertex) {
		t.Error("a healthy reading was cached: this replica would keep sending to a " +
			"provider already known to be down, for a whole trust window")
	}
}

// A positive reading IS cached, so a shedding replica does not pay a Redis round
// trip per refused request. Verified by removing the key underneath a sharer that
// has already read it — while the trust window holds, it answers from memory.
//
// Uses a nonexistent second key to prove the read really is served locally: a
// sharer that went back to Redis would see the deletion.
func TestBreakerSharerCachesOpenReadings(t *testing.T) {
	rdb := testutil.RequireRedis(t)
	ctx := context.Background()

	sharer := newBreakerSharer(rdb, time.Minute)
	sharer.publishOpen(ctx, rtVertex)

	if !sharer.isOpenElsewhere(ctx, rtVertex) {
		t.Fatal("first read should see the published flag")
	}

	if err := rdb.Del(ctx, breakerKey(rtVertex)).Err(); err != nil {
		t.Fatalf("deleting the key: %v", err)
	}

	if !sharer.isOpenElsewhere(ctx, rtVertex) {
		t.Error("the open reading was not cached, so every shed request would pay a " +
			"Redis round trip while the gateway is already degraded")
	}
}

// A stale positive is dropped once Redis says otherwise, so a recovered provider
// is not shed for the rest of a trust window.
//
// The trust window is ttl/4, so a short ttl gives a short window without changing
// the mechanism.
func TestBreakerSharerForgetsOpenAfterTrustWindow(t *testing.T) {
	rdb := testutil.RequireRedis(t)
	ctx := context.Background()

	sharer := newBreakerSharer(rdb, 200*time.Millisecond) // trust window 50ms
	sharer.publishOpen(ctx, rtVertex)
	if !sharer.isOpenElsewhere(ctx, rtVertex) {
		t.Fatal("first read should see the published flag")
	}

	// Remove the flag as an expiry would, then wait out the trust window.
	if err := rdb.Del(ctx, breakerKey(rtVertex)).Err(); err != nil {
		t.Fatalf("deleting the key: %v", err)
	}

	testutil.RequireEventually(t, 2*time.Second, 10*time.Millisecond, func() bool {
		return !sharer.isOpenElsewhere(ctx, rtVertex)
	}, "a cached open must lapse once the trust window passes and Redis no longer agrees")
}

// A dead Redis must not shed traffic. The sharer was added to make failures
// cheaper, so it must never be able to manufacture them.
func TestBreakerSharerFailsOpenWhenRedisIsDown(t *testing.T) {
	// offlineLimiter's client points at a reserved port where nothing listens;
	// reuse that idea directly rather than depending on a limiter here.
	sharer := newBreakerSharer(deadRedis(), 20*time.Second)

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	if sharer.isOpenElsewhere(ctx, rtVertex) {
		t.Error("Redis is unreachable and the sharer reported the provider open — " +
			"a Redis outage would then shed every request on its own")
	}
	// Publishing must not panic or block either; the local breaker has already
	// tripped and this is best-effort notification.
	sharer.publishOpen(ctx, rtVertex)
}

// A nil sharer is the single-replica configuration: fully usable, does nothing.
// Every call site relies on this instead of branching on whether Redis is wired.
func TestNilBreakerSharerIsANoOp(t *testing.T) {
	var sharer *BreakerSharer
	ctx := context.Background()

	if sharer.isOpenElsewhere(ctx, rtVertex) {
		t.Error("a nil sharer must never report a provider open")
	}
	sharer.publishOpen(ctx, rtVertex) // must not panic

	// And newBreakerSharer yields nil for a nil client, so main can wire it
	// unconditionally.
	if got := newBreakerSharer(nil, time.Second); got != nil {
		t.Errorf("newBreakerSharer(nil, …) = %v, want nil", got)
	}
}
