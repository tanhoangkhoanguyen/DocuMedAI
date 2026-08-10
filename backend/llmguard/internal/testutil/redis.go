package testutil

// Redis fixtures for tests that need a live server — the rate limiter's token
// bucket lives in a Lua script and cannot be exercised without one.

import (
	"context"
	"os"
	"testing"
	"time"

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
