package gateway

// Package gateway holds LLMGuard's request pipeline: configuration, the
// rate limiter, the deduper, the circuit breaker + retry loop, the proxy
// handler and its Prometheus metrics.
//
// It lives under internal/ so nothing outside this module can depend on it,
// which keeps the pipeline free to change shape without breaking an external
// caller. `main` is a thin composition root that wires what this file exports
// and owns nothing else.
//
// This file is the ENTIRE public surface. Everything else in the package stays
// unexported, so the seam between `main` and the pipeline is one short file
// rather than a rule someone has to remember. Tests live beside the code they
// exercise and use the unexported forms directly — no identifier is exported
// merely to be testable.

import (
	"context"
	"log/slog"

	"github.com/redis/go-redis/v9"
)

// LoadConfig reads the environment and applies production defaults.
func LoadConfig() Config { return loadConfig() }

// SetupProviders constructs and registers the configured provider adapter and
// its model routing rules. It performs credential work (for Vertex, resolving
// Application Default Credentials), so a process that cannot mint credentials
// fails here at startup rather than on the first request.
func SetupProviders(ctx context.Context, cfg Config) error { return setupProviders(ctx, cfg) }

// NewMetrics registers the pipeline's Prometheus collectors on the default
// registry.
func NewMetrics() *Metrics { return newMetrics() }

// NewRateLimiter builds the Redis-backed token bucket shared across replicas.
func NewRateLimiter(rdb *redis.Client, rpm, burst int) *RateLimiter {
	return newRateLimiter(rdb, rpm, burst)
}

// NewDeduper builds the in-process singleflight deduper.
func NewDeduper() *Deduper { return newDeduper() }

// NewProxy assembles the /v1/chat/completions handler. Dependencies are passed
// in rather than constructed inside, which is what makes the pipeline testable
// without a live Redis or a real provider.
func NewProxy(cfg Config, limiter *RateLimiter, deduper *Deduper, m *Metrics, log *slog.Logger) *Proxy {
	return newProxy(cfg, limiter, deduper, m, log)
}

// Getenv reads an environment variable with a fallback. Exported so `main`'s
// healthcheck can resolve the same PROXY_PORT default the config uses, instead
// of duplicating the literal.
func Getenv(key, def string) string { return getenv(key, def) }
