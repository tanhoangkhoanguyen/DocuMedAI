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
	"time"

	"github.com/redis/go-redis/v9"
)

// LoadConfig reads the environment and applies production defaults.
func LoadConfig() Config { return loadConfig() }

// LoadModelConfig reads and validates the model allowlist.
//
// The file is REQUIRED. It is the only authority over which models LLMGuard will
// serve, so booting without one would leave a gateway that refuses every request
// while its health check stays green — worse than refusing to start.
func LoadModelConfig(path string) (*ModelConfig, error) { return loadModelConfig(path) }

// SetupProviders constructs and registers an adapter per declared provider, then
// installs the allowlist's model routes. It performs credential work (for Vertex,
// resolving Application Default Credentials), so a process that cannot mint
// credentials fails here at startup rather than on the first request.
func SetupProviders(ctx context.Context, cfg Config, mc *ModelConfig) error {
	return setupProviders(ctx, cfg, mc)
}

// NewMetrics registers the pipeline's Prometheus collectors on the default
// registry.
func NewMetrics() *Metrics { return newMetrics() }

// SetupTracing installs an OpenTelemetry tracer provider and returns its shutdown
// function, which flushes spans that are still batched.
//
// Tracing is OFF unless cfg.TraceEndpoint is set, and off means nothing is
// installed. The returned shutdown is a working no-op in that case, so main calls
// it unconditionally rather than tracking whether tracing came up.
func SetupTracing(ctx context.Context, cfg Config) (func(context.Context) error, error) {
	return setupTracing(ctx, cfg)
}

// NewUsageWriter builds the ClickHouse usage log: a bounded, fire-and-forget
// writer that batches per-request rows for cost analytics.
//
// An empty cfg.ClickHouseAddr returns (nil, nil) — the usage log is off, and a
// nil *UsageWriter is a working no-op, so main needs no conditional around it.
//
// An unreachable ClickHouse is NOT an error here: the gateway has no depends_on
// for it, so losing a startup race with a still-booting container must not
// permanently disable the log. Only options the driver rejects outright are
// returned as an error, and even that is not worth exiting for — a usage row is
// not required to serve a request correctly.
func NewUsageWriter(
	ctx context.Context, cfg Config, m *Metrics, log *slog.Logger,
) (*UsageWriter, error) {
	return newUsageWriter(ctx, cfg, m, log)
}

// NewRateLimiter builds the Redis-backed token bucket shared across replicas.
func NewRateLimiter(rdb *redis.Client, rpm, burst int) *RateLimiter {
	return newRateLimiter(rdb, rpm, burst)
}

// NewDeduper builds the in-process singleflight deduper.
func NewDeduper() *Deduper { return newDeduper() }

// NewBreakerSharer builds the cross-replica circuit-breaker signal. It shares the
// Redis client with the rate limiter; a nil client yields a nil sharer, which is a
// working no-op for a single-replica deployment.
func NewBreakerSharer(rdb *redis.Client, openFor time.Duration) *BreakerSharer {
	return newBreakerSharer(rdb, openFor)
}

// NewProxy assembles the /v1/chat/completions handler. Dependencies are passed
// in rather than constructed inside, which is what makes the pipeline testable
// without a live Redis or a real provider.
//
// sharer may be nil, disabling cross-replica breaker propagation.
func NewProxy(
	cfg Config, limiter *RateLimiter, deduper *Deduper,
	sharer *BreakerSharer, m *Metrics, log *slog.Logger,
) *Proxy {
	return newProxy(cfg, limiter, deduper, sharer, m, log)
}

// Getenv reads an environment variable with a fallback. Exported so `main`'s
// healthcheck can resolve the same PROXY_PORT default the config uses, instead
// of duplicating the literal.
func Getenv(key, def string) string { return getenv(key, def) }
