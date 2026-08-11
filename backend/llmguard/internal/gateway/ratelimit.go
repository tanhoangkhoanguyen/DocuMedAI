package gateway

import (
	"context"
	"time"

	"github.com/redis/go-redis/v9"
)

// RateLimiter is a distributed token bucket backed by Redis (diagram box 1).
//
// Why Redis and not an in-memory limiter: the bucket state must be shared if the
// service is ever scaled to >1 replica, so every replica throttles against the
// SAME provider quota. The bucket is keyed per (api-key + model) so a burst on
// one model doesn't starve another.
//
// The refill+take is done in a single Lua script so it is ATOMIC under
// concurrency — no read-modify-write race between competing requests.
type RateLimiter struct {
	rdb   *redis.Client
	rpm   int // sustained requests/min → refill rate
	burst int // bucket capacity
}

// tokenBucketScript implements lazy refill: instead of a background ticker we
// compute how many tokens should have refilled since the bucket was last
// touched, top up (capped at burst), then try to take one.
//
// KEYS[1] = bucket key
// ARGV[1] = capacity (burst)
// ARGV[2] = refill rate (tokens per second)
// ARGV[3] = now (unix seconds, float) — passed in so the script is deterministic
// Returns: 1 if a token was granted, 0 otherwise.
var tokenBucketScript = redis.NewScript(`
local cap   = tonumber(ARGV[1])
local rate  = tonumber(ARGV[2])
local now   = tonumber(ARGV[3])

local data    = redis.call("HMGET", KEYS[1], "tokens", "ts")
local tokens  = tonumber(data[1])
local ts      = tonumber(data[2])
if tokens == nil then tokens = cap; ts = now end

-- lazy refill since last touch, capped at capacity
local delta = math.max(0, now - ts)
tokens = math.min(cap, tokens + delta * rate)

local allowed = 0
if tokens >= 1 then
	tokens = tokens - 1
	allowed = 1
end

redis.call("HMSET", KEYS[1], "tokens", tokens, "ts", now)
-- expire idle buckets so Redis doesn't grow unbounded
redis.call("PEXPIRE", KEYS[1], 120000)
return allowed
`)

func newRateLimiter(rdb *redis.Client, rpm, burst int) *RateLimiter {
	return &RateLimiter{rdb: rdb, rpm: rpm, burst: burst}
}

// Acquire blocks until a token is available or maxWait elapses. Returns true if
// a token was granted, false if we should reject the caller with 429.
//
// We poll with a short interval instead of a blocking pop because the token
// bucket refills continuously — a brief wait usually succeeds and smooths bursts
// rather than failing fast.
func (r *RateLimiter) Acquire(ctx context.Context, key string, maxWait time.Duration) bool {
	rate := float64(r.rpm) / 60.0 // tokens per second
	deadline := time.Now().Add(maxWait)

	for {
		ok, err := r.take(ctx, key, rate)
		if err != nil {
			// Fail OPEN: if Redis is unreachable we must not block all LLM
			// traffic. Retry/circuit-breaker downstream still protect upstream.
			return true
		}
		if ok {
			return true
		}
		if time.Now().After(deadline) {
			return false
		}
		// Sleep ~ one token's worth of time (bounded) before retrying.
		wait := time.Duration(1.0/rate*float64(time.Second)) / 4
		if wait < 20*time.Millisecond {
			wait = 20 * time.Millisecond
		}
		// Never sleep past the deadline. The check above runs BEFORE the sleep,
		// so without this clamp Acquire can return up to one full poll interval
		// after maxWait — and maxWait is precisely the knob an operator turns to
		// bound tail latency. At the default 480 RPM the interval is 31ms and the
		// overshoot is invisible; at 12 RPM it is 1.25s on a 5s budget.
		//
		// Clamped after the floor so the floor cannot re-inflate it past the
		// deadline. A non-positive remainder needs no special case: the sleep
		// returns immediately and the deadline check at the top of the next
		// iteration returns false, which is the correct outcome.
		if remaining := time.Until(deadline); wait > remaining {
			wait = remaining
		}
		select {
		case <-ctx.Done():
			return false
		case <-time.After(wait):
		}
	}
}

func (r *RateLimiter) take(ctx context.Context, key string, rate float64) (bool, error) {
	now := float64(time.Now().UnixNano()) / 1e9
	res, err := tokenBucketScript.Run(ctx, r.rdb,
		[]string{"llmguard:bucket:" + key},
		r.burst, rate, now,
	).Int()
	if err != nil {
		return false, err
	}
	return res == 1, nil
}
