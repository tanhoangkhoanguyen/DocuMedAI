// Package mockupstream is a standalone, deterministic stand-in for an
// OpenAI-compatible or Gemini-native LLM provider.
//
// It exists so the proxy's resilience behavior — retry/backoff, the circuit
// breaker, singleflight dedup, Retry-After handling — can be driven against a
// real upstream over a real socket, in its own process, without spending money
// or depending on a provider's availability.
//
// This is a library with a thin cmd/mockupstream binary on top rather than a
// bare `package main`, so an in-process test fake never has to redefine the
// response shapes: wrap New() in an httptest.Server and the wire format is
// identical by construction. Nothing here imports "testing".
//
// # Determinism
//
// The same request under the same config yields byte-identical responses, which
// is what makes the mock usable as a benchmark baseline. Two rules deliver that:
//
//  1. Response CONTENT is a pure function of the request. Ids, timestamps, token
//     counts and generated text derive from the request bytes and static config
//     only — never time.Now(), never a package-level rand.
//  2. Randomized BEHAVIOR (the error-rate roll, latency jitter) uses a
//     per-request RNG seeded from a hash of the request itself, not a shared
//     generator. See seedFor in chaos.go for why that survives concurrency.
package mockupstream

import (
	"fmt"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config is the mock's static configuration, assembled from flags and the
// environment at startup. Per-request headers and query parameters override it;
// see Resolve.
type Config struct {
	// Addr is the listen address, e.g. ":8090".
	Addr string

	// Latency is a fixed delay added to every response.
	Latency time.Duration
	// Jitter is the width of an additional random delay drawn from [0, Jitter].
	// The draw is seeded per request, so it is reproducible.
	Jitter time.Duration
	// ChunkDelay is the pause between streamed SSE chunks. It shapes
	// time-to-first-token without changing any response bytes.
	ChunkDelay time.Duration

	// ErrorRate is the fraction of requests [0,1] that fail with ErrorStatus.
	// 0 disables failures; 1 fails everything.
	ErrorRate float64
	// ErrorStatus is the status returned by an injected failure.
	ErrorStatus int
	// RetryAfter, when > 0, is emitted as a Retry-After header (delta-seconds)
	// on injected failures. The proxy honors only the delta-seconds form.
	RetryAfter int

	// Seed is mixed into every per-request RNG seed. Changing it reshuffles
	// which requests fail without changing the failure RATE.
	Seed uint64

	// Model is the model name echoed back when the request does not name one.
	Model string
	// Created is the fixed unix timestamp placed in OpenAI responses. A constant
	// rather than time.Now(), because a clock in the response body would break
	// byte-for-byte reproducibility.
	Created int64
	// CompletionTokens is how many words the canned completion contains when the
	// request does not ask for a specific length.
	CompletionTokens int
	// Content, when non-empty, replaces the generated completion text.
	Content string
}

// DefaultCreated is 2025-01-01T00:00:00Z — an arbitrary but FIXED instant. Any
// wall clock here would make responses differ between runs.
const DefaultCreated int64 = 1735689600

// DefaultConfig returns the configuration used when nothing is overridden.
// Failures are off by default so a freshly started mock is a well-behaved
// upstream; tests opt into chaos explicitly.
func DefaultConfig() Config {
	return Config{
		Addr:             ":8090",
		ErrorStatus:      http.StatusServiceUnavailable,
		Seed:             1,
		Model:            "gemini-2.5-flash",
		Created:          DefaultCreated,
		CompletionTokens: 24,
	}
}

// FromEnv layers MOCK_* environment variables over a base config. Unset or
// unparseable values leave the base untouched, so a typo degrades to the
// default rather than to zero.
func FromEnv(base Config) Config {
	cfg := base
	if v := os.Getenv("MOCK_ADDR"); v != "" {
		cfg.Addr = v
	}
	if v := os.Getenv("MOCK_PORT"); v != "" {
		cfg.Addr = ":" + strings.TrimPrefix(v, ":")
	}
	cfg.Latency = envDuration("MOCK_LATENCY", cfg.Latency)
	cfg.Jitter = envDuration("MOCK_JITTER", cfg.Jitter)
	cfg.ChunkDelay = envDuration("MOCK_CHUNK_DELAY", cfg.ChunkDelay)
	cfg.ErrorRate = envFloat("MOCK_ERROR_RATE", cfg.ErrorRate)
	cfg.ErrorStatus = envInt("MOCK_ERROR_STATUS", cfg.ErrorStatus)
	cfg.RetryAfter = envInt("MOCK_RETRY_AFTER", cfg.RetryAfter)
	cfg.Seed = uint64(envInt("MOCK_SEED", int(cfg.Seed)))
	cfg.CompletionTokens = envInt("MOCK_COMPLETION_TOKENS", cfg.CompletionTokens)
	cfg.Created = int64(envInt("MOCK_CREATED", int(cfg.Created)))
	if v := os.Getenv("MOCK_MODEL"); v != "" {
		cfg.Model = v
	}
	if v := os.Getenv("MOCK_CONTENT"); v != "" {
		cfg.Content = v
	}
	return cfg
}

// Resolve produces the effective config for one request by layering, in
// increasing order of precedence: the server's base config, query parameters,
// then X-Mock-* headers.
//
// Headers win because a test client often cannot control the URL (it is built
// by the proxy under test) but can always add a header.
func Resolve(base Config, r *http.Request) Config {
	cfg := base
	q := r.URL.Query()

	// --- query parameters ---
	cfg.Latency = pickDuration(q.Get("latency"), cfg.Latency)
	cfg.Jitter = pickDuration(q.Get("jitter"), cfg.Jitter)
	cfg.ChunkDelay = pickDuration(q.Get("chunk_delay"), cfg.ChunkDelay)
	cfg.ErrorRate = pickFloat(q.Get("error_rate"), cfg.ErrorRate)
	cfg.ErrorStatus = pickInt(q.Get("error_status"), cfg.ErrorStatus)
	cfg.RetryAfter = pickInt(q.Get("retry_after"), cfg.RetryAfter)
	cfg.Seed = uint64(pickInt(q.Get("seed"), int(cfg.Seed)))
	cfg.CompletionTokens = pickInt(q.Get("completion_tokens"), cfg.CompletionTokens)
	if v := q.Get("content"); v != "" {
		cfg.Content = v
	}

	// --- headers (highest precedence) ---
	h := r.Header
	cfg.Latency = pickDuration(h.Get("X-Mock-Latency"), cfg.Latency)
	cfg.Jitter = pickDuration(h.Get("X-Mock-Jitter"), cfg.Jitter)
	cfg.ChunkDelay = pickDuration(h.Get("X-Mock-Chunk-Delay"), cfg.ChunkDelay)
	cfg.ErrorRate = pickFloat(h.Get("X-Mock-Error-Rate"), cfg.ErrorRate)
	cfg.ErrorStatus = pickInt(h.Get("X-Mock-Error-Status"), cfg.ErrorStatus)
	cfg.RetryAfter = pickInt(h.Get("X-Mock-Retry-After"), cfg.RetryAfter)
	cfg.Seed = uint64(pickInt(h.Get("X-Mock-Seed"), int(cfg.Seed)))
	cfg.CompletionTokens = pickInt(h.Get("X-Mock-Completion-Tokens"), cfg.CompletionTokens)
	if v := h.Get("X-Mock-Content"); v != "" {
		cfg.Content = v
	}

	if cfg.ErrorStatus == 0 {
		cfg.ErrorStatus = http.StatusServiceUnavailable
	}
	if cfg.CompletionTokens < 0 {
		cfg.CompletionTokens = 0
	}
	return cfg
}

// fingerprint is the subset of config that can change a response, rendered as a
// string and folded into the per-request RNG seed. Latency and ChunkDelay are
// excluded on purpose: they alter timing, never bytes, so including them would
// make two configs that differ only in speed produce different failure verdicts.
func (c Config) fingerprint() string {
	return fmt.Sprintf("%g|%d|%d|%d|%s|%d|%d|%s",
		c.ErrorRate, c.ErrorStatus, c.RetryAfter, c.Seed,
		c.Model, c.Created, c.CompletionTokens, c.Content)
}

// --- parsing helpers -------------------------------------------------------

func pickDuration(v string, def time.Duration) time.Duration {
	if v == "" {
		return def
	}
	d, err := time.ParseDuration(v)
	if err != nil || d < 0 {
		return def
	}
	return d
}

func pickFloat(v string, def float64) float64 {
	if v == "" {
		return def
	}
	f, err := strconv.ParseFloat(v, 64)
	if err != nil {
		return def
	}
	switch {
	case f < 0:
		return 0
	case f > 1:
		return 1
	default:
		return f
	}
}

func pickInt(v string, def int) int {
	if v == "" {
		return def
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return def
	}
	return n
}

func envDuration(key string, def time.Duration) time.Duration {
	return pickDuration(os.Getenv(key), def)
}

func envFloat(key string, def float64) float64 {
	return pickFloat(os.Getenv(key), def)
}

func envInt(key string, def int) int {
	return pickInt(os.Getenv(key), def)
}
