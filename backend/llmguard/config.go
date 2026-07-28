package main

import (
	"os"
	"strconv"
	"time"
)

// Config holds every tunable knob LLMGuard reads from the environment.
//
// In docker-compose these come from `.env` + explicit `environment:` overrides
// on the `la-llmguard` service. Defaults are chosen so it boots with
// nothing but OPENAI_API_KEY set.
type Config struct {
	// Provider selects the default adapter for models that match no routing
	// rule. Read from LLMGUARD_PROVIDER.
	Provider string

	// VertexProject / VertexLocation configure the Vertex AI adapter. These are
	// the SAME env vars the Python backend reads (backend/utils/llm_config.py),
	// so one .env configures both. Auth is Application Default Credentials —
	// there is no API key: Vertex will not accept one.
	VertexProject  string
	VertexLocation string

	// Port the proxy listens on (matches docker-compose + the client base_url).
	Port string

	// RedisURL backs the distributed token bucket and the cross-process dedup
	// marker. We use DB 1 so we never collide with the app's chat cache on DB 0.
	RedisURL string

	// --- Rate limiting (token bucket, per API key + model) ---
	RateLimitRPM   int // sustained requests/min that refill the bucket
	RateLimitBurst int // max tokens the bucket can hold (allows short bursts)
	RateWaitMax    time.Duration // how long a request blocks for a token before we 429

	// --- Retry / backoff ---
	RetryMax     int           // max attempts (1 = no retry)
	RetryBaseDly time.Duration // base delay for exponential backoff
	RetryMaxDly  time.Duration // cap on a single backoff sleep

	// --- Circuit breaker ---
	CircuitMinReqs    uint32        // min requests in a window before the breaker may trip
	CircuitFailRatio  float64       // fraction of failures that trips the breaker
	CircuitOpenFor    time.Duration // how long the breaker stays open before half-open probe

	// --- Upstream HTTP client ---
	UpstreamTimeout time.Duration // per-attempt timeout to upstream
	MaxIdleConns    int           // connection-pool size for keep-alive reuse
}

// loadConfig reads the environment and applies sensible production defaults.
func loadConfig() Config {
	return Config{
		Provider:       getenv("LLMGUARD_PROVIDER", "vertex"),
		VertexProject:  os.Getenv("GOOGLE_CLOUD_PROJECT"),
		VertexLocation: getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
		Port:           getenv("PROXY_PORT", "8081"),
		RedisURL:       getenv("REDIS_URL", "redis://la-redis:6379/1"),

		RateLimitRPM:   getenvInt("RATE_LIMIT_RPM", 480),                // 8 req/s sustained
		RateLimitBurst: getenvInt("RATE_LIMIT_BURST", 60),
		RateWaitMax:    getenvDur("RATE_WAIT_MAX", 5*time.Second),

		RetryMax:     getenvInt("RETRY_MAX", 4),
		RetryBaseDly: getenvDur("RETRY_BASE_DELAY", 300*time.Millisecond),
		RetryMaxDly:  getenvDur("RETRY_MAX_DELAY", 8*time.Second),

		CircuitMinReqs:   uint32(getenvInt("CIRCUIT_MIN_REQUESTS", 10)),
		CircuitFailRatio: getenvFloat("CIRCUIT_FAIL_RATIO", 0.6),
		CircuitOpenFor:   getenvDur("CIRCUIT_OPEN_FOR", 20*time.Second),

		UpstreamTimeout: getenvDur("UPSTREAM_TIMEOUT", 120*time.Second), // LLM calls can be slow
		MaxIdleConns:    getenvInt("MAX_IDLE_CONNS", 100),
	}
}

// --- tiny env helpers (kept here so the rest of the code stays clean) ---
func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func getenvInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func getenvFloat(key string, def float64) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}

func getenvDur(key string, def time.Duration) time.Duration {
	if v := os.Getenv(key); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
	}
	return def
}
