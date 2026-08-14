package gateway

// Cross-replica circuit-breaker state.
//
// The breaker in retry.go is per PROCESS, which is correct for one replica and
// wrong for several. Each replica has to learn an outage independently, so at N
// replicas an upstream absorbs N × CircuitMinReqs doomed requests before anything
// trips — and a replica that restarts mid-outage starts from a clean slate and
// hammers a provider the others already know is down. Both are the opposite of
// what a breaker is for.
//
// What crosses replicas is the TRIP SIGNAL, not the counters. The local breaker
// stays the authority on its own decisions:
//
//   - Sharing counters would put a Redis round trip on every request's hot path
//     and make Redis a hard dependency of a component that currently has none.
//   - A trip is a single fact with a natural lifetime (CircuitOpenFor) and needs
//     no consensus: "provider X was found to be down recently" is safe for any
//     replica to act on, and stale-by-a-few-seconds is harmless because the local
//     breaker's own half-open probe still governs recovery.
//
// So: publish on open, read a cached view on each request, and fail open on any
// Redis error — the same posture as the rate limiter, and for the same reason. A
// broken Redis must not be able to shed traffic that would otherwise succeed.

import (
	"context"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

// BreakerSharer publishes and reads the "provider is open" flag.
//
// A nil *BreakerSharer is fully usable and does nothing, so single-replica
// deployments and the whole test suite need no Redis and no branch at the call
// sites — every method below tolerates a nil receiver.
type BreakerSharer struct {
	rdb *redis.Client
	ttl time.Duration

	// mu guards openUntil. A plain Mutex: the critical section is a map read and a
	// time comparison, nothing that outweighs an upstream LLM call.
	mu sync.Mutex
	// openUntil caches only the POSITIVE reading, per provider: "another replica
	// reported this down, and that is worth believing until at least this time".
	//
	// Caching only one polarity is the important part. A cached NEGATIVE would
	// delay this replica's entry into an outage by up to a refresh interval —
	// which is precisely the lateness the shared flag exists to remove, so
	// remembering "healthy" would defeat the feature to save a round trip on the
	// path that is already working. A cached POSITIVE costs nothing by comparison:
	// the request is being shed either way, and the worst case is shedding for a
	// few seconds longer than strictly necessary, which the local breaker would
	// have done anyway.
	//
	// So the round trip is paid while healthy (once per request, on a local Redis
	// alongside the rate limiter's own call) and skipped while shedding, when
	// there is no upstream call to amortize it against.
	openUntil map[string]time.Time
}

func newBreakerSharer(rdb *redis.Client, ttl time.Duration) *BreakerSharer {
	if rdb == nil {
		return nil
	}
	return &BreakerSharer{rdb: rdb, ttl: ttl, openUntil: map[string]time.Time{}}
}

// trustOpenFor is how long a positive reading is believed without re-checking.
//
// A quarter of the open window, so a shedding replica re-checks a few times
// before the flag would expire anyway and cannot keep shedding after the
// publisher recovered. At the default CircuitOpenFor=20s that is 5s.
func (s *BreakerSharer) trustOpenFor() time.Duration {
	if s.ttl <= 0 {
		return time.Second
	}
	return s.ttl / 4
}

func breakerKey(providerName string) string {
	return "llmguard:breaker:open:" + providerName
}

// publishOpen records that this replica found the provider unhealthy.
//
// TTL rather than an explicit delete on close: a replica that crashes while the
// flag is set must not leave every other replica shedding forever. Expiry makes
// the failure mode "the flag lapses and replicas re-probe", which is the same
// thing a closing breaker does anyway.
func (s *BreakerSharer) publishOpen(ctx context.Context, providerName string) {
	if s == nil {
		return
	}
	// Errors are dropped deliberately: failing to publish costs the OTHER replicas
	// an early warning, which is a degradation, not a reason to fail this request.
	// The local breaker has already tripped regardless.
	_ = s.rdb.Set(ctx, breakerKey(providerName), "1", s.ttl).Err()
}

// isOpenElsewhere reports whether another replica has recently found this
// provider unhealthy.
//
// While shedding, this answers from the cached positive and costs nothing. While
// healthy it costs one EXISTS — deliberately, per the openUntil comment: the
// request is about to make an upstream LLM call taking seconds, and the rate
// limiter has already contacted the same Redis, so the round trip is not a new
// class of cost. Being late to an outage would be.
func (s *BreakerSharer) isOpenElsewhere(ctx context.Context, providerName string) bool {
	if s == nil {
		return false
	}

	s.mu.Lock()
	trustedUntil, cached := s.openUntil[providerName]
	s.mu.Unlock()
	if cached && time.Now().Before(trustedUntil) {
		return true
	}

	n, err := s.rdb.Exists(ctx, breakerKey(providerName)).Result()
	if err != nil {
		// Fail OPEN, i.e. treat the provider as usable. A Redis outage must never
		// be able to shed traffic on its own — that would let the component added
		// for reliability become a new way to lose every request. The local breaker
		// still protects the upstream.
		return false
	}
	if n == 0 {
		// Drop any stale positive so a recovered provider is not shed for the
		// remainder of a trust window that Redis has already contradicted.
		if cached {
			s.mu.Lock()
			delete(s.openUntil, providerName)
			s.mu.Unlock()
		}
		return false
	}

	s.mu.Lock()
	s.openUntil[providerName] = time.Now().Add(s.trustOpenFor())
	s.mu.Unlock()
	return true
}
