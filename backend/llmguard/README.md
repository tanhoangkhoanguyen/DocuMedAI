# LLMGuard

An **OpenAI-API-compatible** gateway in front of the LLM provider.

```
client (base_url=…)  →  la-llmguard :8081  →  provider adapter  →  Vertex AI
                         ├ rate limit (Redis token bucket)
                         ├ retry + backoff (honors Retry-After)
                         ├ circuit breaker (fail fast on outage)
                         ├ in-flight dedup (singleflight)
                         └ Prometheus /metrics
```

Clients speak the OpenAI wire format. Internally a **provider adapter** translates
to the vendor's native API — today Vertex AI's `generateContent`. Core (rate
limit, dedup, breaker, retry, metrics) never sees vendor JSON, so adding a
provider doesn't change clients or core.

Only chat completions pass through. Embeddings and the reranker run locally in the
backend and never reach LLMGuard.

## Endpoints

| Path | Purpose |
|------|---------|
| `POST /v1/chat/completions` | Translated to the provider's native API |
| `GET /healthz` | Liveness (docker healthcheck uses `-healthcheck`) |
| `GET /metrics` | Prometheus metrics (`llmguard_*`) |

## Configuration (env)

| Var | Default | Notes |
|-----|---------|-------|
| `LLMGUARD_PROVIDER` | `vertex` | Default adapter for models matching no routing rule |
| `GOOGLE_CLOUD_PROJECT` | — | **Required.** Same var the Python backend reads |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | Vertex region — appears in both host and path |
| `PROXY_PORT` | `8081` | |
| `REDIS_URL` | `redis://la-redis:6379/1` | DB 1 — separate from the app cache (DB 0) |
| `RATE_LIMIT_RPM` / `RATE_LIMIT_BURST` / `RATE_WAIT_MAX` | `480` / `60` / `5s` | Token bucket |
| `RETRY_MAX` / `RETRY_BASE_DELAY` / `RETRY_MAX_DELAY` | `4` / `300ms` / `8s` | Backoff |
| `CIRCUIT_MIN_REQUESTS` / `CIRCUIT_FAIL_RATIO` / `CIRCUIT_OPEN_FOR` | `10` / `0.6` / `20s` | Breaker |
| `UPSTREAM_TIMEOUT` / `MAX_IDLE_CONNS` | `120s` / `100` | HTTP client |

**Auth is ADC** — Application Default Credentials (`GOOGLE_APPLICATION_CREDENTIALS`,
gcloud credentials, or the attached service account). Vertex does not accept an API
key. Credentials are resolved at startup: no ADC → the process exits rather than
failing on the first request. LLMGuard is therefore **not started in CI**, which
has no GCP credentials.

## Run

```bash
docker compose up -d --build la-llmguard
curl localhost:8081/healthz    # {"status":"ok"}

curl localhost:8081/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"gemini-2.5-flash","messages":[{"role":"user","content":"hi"}]}'
```

Uncomment the `GOOGLE_APPLICATION_CREDENTIALS` env var and the SA-key volume in
`docker-compose.yml` to supply credentials from a key file.

There is no `go.sum` in the repo — the Dockerfile runs `go mod tidy` in-build, so
compose is the only supported build path.

## Files

| File | Responsibility |
|------|----------------|
| `main.go` | Composition root: wiring, HTTP server, graceful shutdown, `-healthcheck` |
| `internal/gateway/gateway.go` | The package's entire exported surface — what `main` may call |
| `internal/gateway/config.go` | Env-driven config + defaults |
| `internal/gateway/providers.go` | Adapter construction + model routing rules |
| `internal/gateway/proxy.go` | Rate limit → dedup → breaker → retry; SSE translation loop |
| `internal/gateway/ratelimit.go` | Redis token bucket (atomic Lua) |
| `internal/gateway/retry.go` | Backoff + jitter + Retry-After + circuit breaker |
| `internal/gateway/dedup.go` | In-flight de-duplication (singleflight) |
| `internal/gateway/metrics.go` | Prometheus collectors |
| `provider/` | Normalized schema, `Provider` interface + registry, Vertex adapter |
| `mockupstream/` | Deterministic fake provider used by every test (and the benchmark) |
| `internal/testutil/redis.go` | Live-Redis gate for the Lua token-bucket tests |
| `internal/testutil/polling.go` | `Eventually` — waits on state that settles asynchronously |
| `internal/testutil/metrics.go` | Prometheus readers, so tests can assert on instrumentation |

The pipeline sits under `internal/` so nothing outside this module can depend on
it, leaving it free to change shape. Only `gateway.go` is exported; everything
else in the package is unexported, and tests live beside the code they exercise
so no identifier is exported merely to be testable.

## Adding a provider

Implement `provider.Provider` (4 methods: `Name`, `BuildRequest`,
`TranslateResponse`, `TranslateStreamChunk`), then register it in
`internal/gateway/providers.go` with a `RouteModel` prefix rule. Nothing in
`internal/gateway/proxy.go` or the client contract changes.

## Notes

- Vertex reports `thoughtsTokenCount` for thinking models. It is billed as output
  and has no OpenAI field, so it is folded into `completion_tokens`.
- The streaming path now accounts tokens; the pre-Vertex byte-pipe could not.

## Deferred

- Cross-replica dedup via Redis marker (extension point in
  `internal/gateway/dedup.go`).
