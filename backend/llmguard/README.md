# LLMGuard

An **OpenAI-API-compatible** gateway in front of the LLM provider.

```
client (base_url=…)  →  la-llmguard :8081  →  generativelanguage.googleapis.com/v1beta/openai
                         ├ rate limit (Redis token bucket)
                         ├ retry + backoff (honors Retry-After)
                         ├ circuit breaker (fail fast on outage)
                         ├ in-flight dedup (singleflight)
                         └ Prometheus /metrics
```

Only chat completions pass through. Embeddings and the reranker run locally in the
backend and never reach LLMGuard.

## Endpoints

| Path | Purpose |
|------|---------|
| `POST /v1/chat/completions` (and any `/v1/*`) | Proxied upstream with the real key injected |
| `GET /healthz` | Liveness (docker healthcheck uses `-healthcheck`) |
| `GET /metrics` | Prometheus metrics (`llmguard_*`) |

## Configuration (env)

| Var | Default | Notes |
|-----|---------|-------|
| `OPENAI_UPSTREAM_BASE` | `https://api.openai.com/v1` | Must **not** end in `/v1`: a leading `/v1` is stripped from inbound paths before appending |
| `PROXY_PORT` | `8081` | |
| `REDIS_URL` | `redis://la-redis:6379/1` | DB 1 — separate from the app cache (DB 0) |
| `RATE_LIMIT_RPM` / `RATE_LIMIT_BURST` / `RATE_WAIT_MAX` | `480` / `60` / `5s` | Token bucket |
| `RETRY_MAX` / `RETRY_BASE_DELAY` / `RETRY_MAX_DELAY` | `4` / `300ms` / `8s` | Backoff |
| `CIRCUIT_MIN_REQUESTS` / `CIRCUIT_FAIL_RATIO` / `CIRCUIT_OPEN_FOR` | `10` / `0.6` / `20s` | Breaker |
| `UPSTREAM_TIMEOUT` / `MAX_IDLE_CONNS` | `120s` / `100` | HTTP client |

## Run

```bash
docker compose up -d --build la-llmguard
curl localhost:8081/healthz    # {"status":"ok"}
curl localhost:8081/metrics
```

There is no `go.sum` in the repo — the Dockerfile runs `go mod tidy` in-build, so
compose is the only supported build path.

## Files

| File | Responsibility |
|------|----------------|
| `main.go` | Wiring, HTTP server, graceful shutdown, `-healthcheck` |
| `config.go` | Env-driven config + defaults |
| `proxy.go` | Request handling, passthrough, streaming, key injection, usage metrics |
| `ratelimit.go` | Redis token bucket (atomic Lua) |
| `retry.go` | Backoff + jitter + Retry-After + circuit breaker |
| `dedup.go` | In-flight de-duplication (singleflight) |
| `metrics.go` | Prometheus collectors |

## Deferred

- Vertex AI adapter behind a provider abstraction (next commit).
- Go tests + CI — there are none today.
- Cross-replica dedup via Redis marker (extension point in `dedup.go`).
