package testutil

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/redis/go-redis/v9"
)

// DefaultRedisURL is the fallback Redis endpoint for tests.
//
// It points at localhost DB 15 rather than the app's DB 0 or the proxy's own
// DB 1, so a test run can never clobber real cached state. Override with
// TEST_REDIS_URL when Redis lives elsewhere (for example inside compose, where
// the host is la-redis).
const DefaultRedisURL = "redis://127.0.0.1:6379/15"

// RedisURL returns the Redis endpoint tests should use.
func RedisURL() string {
	if v := os.Getenv("TEST_REDIS_URL"); v != "" {
		return v
	}
	return DefaultRedisURL
}

// RequireRedis returns a Redis client for tests that genuinely need one — the
// rate limiter's token bucket lives in a Lua script, so it cannot be exercised
// without a real server.
//
// If Redis is unreachable the test is SKIPPED, not failed. `make test` has to
// stay green on a laptop with nothing running; CI is where Redis is guaranteed,
// and there the skip never fires. The client and a flush of the test DB are
// registered with t.Cleanup so state cannot leak between tests.
func RequireRedis(t *testing.T) *redis.Client {
	t.Helper()

	url := RedisURL()
	opt, err := redis.ParseURL(url)
	if err != nil {
		t.Fatalf("testutil: invalid redis url %q: %v", url, err)
	}

	client := redis.NewClient(opt)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	if pingErr := client.Ping(ctx).Err(); pingErr != nil {
		_ = client.Close()
		t.Skipf("testutil: redis unavailable at %s (%v); skipping", url, pingErr)
	}

	t.Cleanup(func() {
		flushCtx, flushCancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer flushCancel()
		_ = client.FlushDB(flushCtx).Err()
		_ = client.Close()
	})
	return client
}

// Eventually polls cond until it returns true or timeout elapses.
//
// The proxy is full of state that settles asynchronously — the circuit breaker
// reopening after its timeout, a metric incremented on a background goroutine —
// and a bare sleep is either flaky or slow. Returns true if cond ever held.
func Eventually(timeout, interval time.Duration, cond func() bool) bool {
	if interval <= 0 {
		interval = 10 * time.Millisecond
	}
	deadline := time.Now().Add(timeout)
	for {
		if cond() {
			return true
		}
		if time.Now().After(deadline) {
			return false
		}
		time.Sleep(interval)
	}
}

// RequireEventually is Eventually with a failure attached, for the common case
// where the condition not holding means the test failed.
func RequireEventually(t *testing.T, timeout, interval time.Duration, cond func() bool, msg string) {
	t.Helper()
	if !Eventually(timeout, interval, cond) {
		t.Fatalf("testutil: condition never held within %s: %s", timeout, msg)
	}
}

// CounterValue reads the current value of a single (non-vector) counter or
// gauge, so tests can assert on the proxy's Prometheus instrumentation —
// dedup hits, retries burned, breaker state — instead of only on HTTP output.
func CounterValue(t *testing.T, c prometheus.Collector) float64 {
	t.Helper()
	return testutil.ToFloat64(c)
}

// LabeledCounterValue reads one labeled child out of a CounterVec, matching
// the label order declared when the vector was created.
func LabeledCounterValue(t *testing.T, vec *prometheus.CounterVec, labels ...string) float64 {
	t.Helper()
	counter, err := vec.GetMetricWithLabelValues(labels...)
	if err != nil {
		t.Fatalf("testutil: bad labels %v: %v", labels, err)
	}
	return testutil.ToFloat64(counter)
}

// FreeRedisDB is a convenience for tests that want an isolated keyspace without
// a dedicated server: it flushes the configured test DB up front, so a run
// starts from a known-empty bucket set.
func FreeRedisDB(t *testing.T, client *redis.Client) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := client.FlushDB(ctx).Err(); err != nil {
		t.Fatalf("testutil: flush test redis db: %v", err)
	}
}
