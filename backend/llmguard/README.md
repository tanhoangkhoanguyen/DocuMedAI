# LLMGuard

An **OpenAI-API-compatible** gateway in front of the LLM provider.

```
client (base_url=…)  →  la-llmguard :8081  →  provider adapter  →  Vertex AI
                         ├ model allowlist (exact, per provider+model)
                         ├ rate limit (Redis token bucket)
                         ├ retry + backoff (honors Retry-After)
                         ├ circuit breaker (one per provider, fail fast on outage)
                         ├ in-flight dedup (singleflight)
                         └ Prometheus /metrics
```

Clients speak the OpenAI wire format. Internally a **provider adapter** translates
to the vendor's native API — Vertex AI's `generateContent`, or any endpoint that
already speaks OpenAI. Core (rate limit, dedup, breaker, retry, metrics) never
sees vendor JSON, so adding a provider doesn't change clients or core.

Only chat completions pass through. Embeddings and the reranker run locally in the
backend and never reach LLMGuard.

## The model allowlist

A request names **both** an upstream and a model:

```json
{"provider": "vertex-prod", "model": "gemini-2.5-flash", "messages": [...]}
```

`provider` is an LLMGuard extension, not part of the OpenAI schema. It exists
because one model can be served by several upstreams, and the OpenAI wire format
has only the one `model` field to tell them apart. It is required — LLMGuard will
not pick an upstream on the caller's behalf — and is stripped before the request
goes upstream.

`config.yaml` (path from `LLMGUARD_CONFIG`) declares the upstreams and which
models each may serve:

```yaml
version: 1

providers:
  - name: vertex-prod          # your name for this upstream; also its metrics
    type: vertex               # and circuit-breaker key
    project_env: GOOGLE_CLOUD_PROJECT   # an env var NAME, never a value
    location: us-central1

  - name: openrouter
    type: openai-compat
    base_url: https://openrouter.ai/api/v1   # no /chat/completions suffix
    api_key_env: OPENROUTER_API_KEY

model_list:
  - model_name: gemini-2.5-flash
    provider: vertex-prod
    pricing: {input_per_1k: 0.000075, output_per_1k: 0.0003}

  # The same model through a second upstream: a separate route, addressed as
  # provider "openrouter". Neither entry shadows the other.
  - model_name: gemini-2.5-flash
    provider: openrouter

  - model_name: gpt-4o-mini
    provider: openrouter
    upstream_model: openai/gpt-4o-mini   # what the vendor calls it
```

Matching is **exact** on the `(provider, model)` pair — no prefixes, no fallback
to a default adapter. An unlisted pair is refused with a 400 that names the
enabled routes; nothing reaches an upstream. The file is required, and every
validation problem is reported at once, because an optional allowlist would boot
a gateway that 400s every request while its health check stayed green.

Credentials are never in the file: it names env vars, so a committed config
cannot carry a secret. `pricing` is recorded for a later consumer to attribute
spend — LLMGuard is a reliability gateway and does nothing with those numbers.

## Endpoints

| Path | Purpose |
|------|---------|
| `POST /v1/chat/completions` | Translated to the provider's native API |
| `GET /healthz` | Liveness (docker healthcheck uses `-healthcheck`) |
| `GET /metrics` | Prometheus metrics (`llmguard_*`) |

## Configuration (env)

| Var | Default | Notes |
|-----|---------|-------|
| `LLMGUARD_CONFIG` | `config.yaml` | **Required file.** The model allowlist — see below |
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
  -d '{"provider":"vertex-prod","model":"gemini-2.5-flash",
       "messages":[{"role":"user","content":"hi"}]}'
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
| `internal/gateway/modelconfig.go` | Parses + validates `config.yaml` (the allowlist) |
| `internal/gateway/providers.go` | Adapter construction + installing the allowlist's routes |
| `internal/gateway/proxy.go` | Rate limit → dedup → breaker → retry; SSE translation loop |
| `internal/gateway/ratelimit.go` | Redis token bucket (atomic Lua) |
| `internal/gateway/retry.go` | Backoff + jitter + Retry-After + per-provider circuit breakers |
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

Two cases:

**An upstream that already speaks OpenAI** (OpenRouter, Groq, Together, vLLM,
Gemini's compat endpoint) needs no code — add a `type: openai-compat` entry to
`config.yaml` with its `base_url` and key.

**A genuinely different wire format** needs an adapter: implement
`provider.Provider` (4 methods: `Name`, `BuildRequest`, `TranslateResponse`,
`TranslateStreamChunk`), then add a case to `buildAdapter` in
`internal/gateway/providers.go`. That function is the only place that knows a
vendor's name; `proxy.go` and the client contract are untouched.

Adapters register under the **config's** provider name, not the adapter type's,
so two instances of one type can coexist and metrics and the circuit breaker key
on something the operator chose.

## Notes

- Vertex reports `thoughtsTokenCount` for thinking models. It is billed as output
  and has no OpenAI field, so it is folded into `completion_tokens`.
- The streaming path now accounts tokens; the pre-Vertex byte-pipe could not.

## Deferred

- Cross-replica dedup via Redis marker (extension point in
  `internal/gateway/dedup.go`).
