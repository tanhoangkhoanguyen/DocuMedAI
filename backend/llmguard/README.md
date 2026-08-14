# LLMGuard

An **OpenAI-API-compatible** gateway in front of the LLM provider.

```
client (base_url=…)  →  la-llmguard :8081  →  provider adapter  →  Vertex AI
                         ├ model allowlist (exact, per provider+model)
                         ├ admission control (in-flight ceiling, sheds 429)
                         ├ rate limit (Redis token bucket)
                         ├ retry + backoff (honors Retry-After)
                         ├ circuit breaker (one per provider, fail fast on outage)
                         ├ in-flight dedup (singleflight)
                         └ Prometheus /metrics
```

Clients speak the OpenAI wire format. Internally a **provider adapter** translates
to the vendor's native API. There are two, and the split matters when you add a
vendor: `provider/openai` is generic and serves every OpenAI-compatible upstream
by configuration alone, while `provider/vertex` is a native adapter for the one
API that cannot be reached that way. Core (rate limit, dedup, breaker, retry,
metrics) never sees vendor JSON, so adding a provider doesn't change clients or
core — see [Adding a provider](#adding-a-provider).

Only chat completions pass through. Embeddings and the reranker run locally in the
backend and never reach LLMGuard.

## The model allowlist

A request names **both** an upstream and a model:

```json
{"provider": "vertex", "model": "gemini-2.5-flash", "messages": [...]}
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
  - name: vertex               # YOUR name for this instance; also its metrics
    type: vertex               # and circuit-breaker key. `type` picks the adapter.
    project_env: GOOGLE_CLOUD_PROJECT   # an env var NAME, never a value
    location: us-central1

  - name: openrouter
    type: openai-compat
    base_url: https://openrouter.ai/api/v1   # no /chat/completions suffix
    api_key_env: OPENROUTER_API_KEY

model_list:
  - model_name: gemini-2.5-flash
    provider: vertex
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
| `MAX_IN_FLIGHT` | `256` | Concurrency ceiling — see below. `0` disables it |
| `UPSTREAM_TIMEOUT` / `MAX_IDLE_CONNS` | `120s` / `100` | HTTP client |
| `SERVER_IDLE_TIMEOUT` | `120s` | Idle keep-alive connections. There is deliberately no write timeout — see `main.go` |

### Admission control

The rate limit and the in-flight ceiling bound **different quantities**, which is why both exist.
`RATE_LIMIT_RPM` bounds how fast requests *arrive*; `MAX_IN_FLIGHT` bounds how many are *running*.
Arrival rate is what a caller's quota is written in. Concurrency is what maps to memory, since each
in-flight request holds a goroutine, a response buffer up to 10 MiB, and an upstream connection.

They diverge exactly when it matters. At 480 RPM with 20s completions ~160 requests are legitimately
in flight; if the upstream slows to 60s, the same admitted rate produces ~480. Arrival rate never
signals that, so a rate limiter alone keeps admitting while memory runs out.

Past the ceiling LLMGuard refuses immediately with **429 + `Retry-After`** — not 503, because the
upstream is healthy and the request is fine; the gateway is full, and 503 would send fail-over traffic
away from a working provider. The refusal is non-blocking on purpose: a queued request still holds the
resources the ceiling exists to bound.

Tune it as:

```
MAX_IN_FLIGHT ≈ (RATE_LIMIT_RPM / 60) × p95_upstream_seconds × 1.5
```

The default 256 is that formula at 480 RPM and a 20s p95, so a bucket-legal burst is never shed. It
engages when requests drain slower than they arrive, or when Redis is down and the rate limiter is
failing open. Watch `llmguard_in_flight` against the ceiling; `llmguard_shed_total` is counted apart
from `llmguard_rate_limited_total` because a caller over quota and a gateway out of capacity are
different incidents that happen to share a status code.

### Auth

Each `type` authenticates differently, and the config never holds the secret
itself — only the NAME of an env var.

| `type` | Credential | Where it comes from |
|--------|-----------|---------------------|
| `openai-compat` | A static API key | The env var named by `api_key_env`. It must be **set**, not merely named: a declared-but-empty key is the same outage as a wrong one, so LLMGuard refuses to start instead of failing on the first call. The caller's own `Authorization` header is discarded — LLMGuard substitutes its own key, so clients never hold the upstream credential. |
| `vertex` | ADC — a short-lived OAuth2 token | Application Default Credentials. Vertex does **not** accept an API key. |

**Vertex / ADC in practice.** `google.DefaultTokenSource` resolves credentials in
the standard order, so the *same binary* authenticates in every environment with
no code change and no config change:

| Environment | What you do |
|-------------|-------------|
| Local, service-account JSON key | Set `GOOGLE_APPLICATION_CREDENTIALS=/path/sa-key.json`. Under Docker, mount the file read-only and point the var at the mount path. |
| Local, your own account | `gcloud auth application-default login` — no env var, no key file. |
| On GCP (Cloud Run, GKE, GCE) | Nothing. Attach a service account to the workload; the token comes from the metadata server. **Do not** ship a key file here. |

There is deliberately no `credentials_file:` field in `config.yaml`: it would be
a second spelling of `GOOGLE_APPLICATION_CREDENTIALS` and buy no capability. A
service-account key is a real secret — mount it or use a secret manager, never
commit it.

Credentials are resolved at startup: no ADC → the process exits rather than
failing on the first request. LLMGuard is therefore **not started in CI**, which
has no GCP credentials.

## Run

```bash
docker compose up -d --build la-llmguard
curl localhost:8081/healthz    # {"status":"ok"}

curl localhost:8081/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"provider":"vertex","model":"gemini-2.5-flash",
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
| `internal/gateway/proxy.go` | Admit → rate limit → dedup → breaker → retry; SSE translation loop |
| `internal/gateway/admission.go` | In-flight ceiling (counting semaphore) + shedding |
| `internal/gateway/ratelimit.go` | Redis token bucket (atomic Lua) |
| `internal/gateway/retry.go` | Backoff + jitter + Retry-After + per-provider circuit breakers |
| `internal/gateway/dedup.go` | In-flight de-duplication (singleflight) |
| `internal/gateway/metrics.go` | Prometheus collectors |
| `provider/` | What every adapter shares: normalized schema, `Provider` interface + registry, error vocabulary |
| `provider/openai/` | The **generic** adapter — every OpenAI-compatible upstream, config-only |
| `provider/vertex/` | The **native** adapter for Vertex AI `generateContent` (+ golden fixtures) |
| `mockupstream/` | Deterministic fake provider used by every test (and the benchmark) |
| `internal/testutil/redis.go` | Live-Redis gate for the Lua token-bucket tests |
| `internal/testutil/polling.go` | `Eventually` — waits on state that settles asynchronously |
| `internal/testutil/metrics.go` | Prometheus readers, so tests can assert on instrumentation |

The pipeline sits under `internal/` so nothing outside this module can depend on
it, leaving it free to change shape. Only `gateway.go` is exported; everything
else in the package is unexported, and tests live beside the code they exercise
so no identifier is exported merely to be testable.

## Adding a provider

There are two tiers, and the first one covers almost everything. **Start by
assuming you need no code.**

**Tier 1 — `type: openai-compat` (the default).** Any upstream that already
speaks OpenAI's `/chat/completions` is reached by configuration alone: OpenAI,
OpenRouter, Groq, Together, DeepSeek, xAI, a self-hosted vLLM, and Gemini's own
compat endpoint. Add four lines to `config.yaml` and you are done — no Go file,
no adapter, no rebuild of the pipeline:

```yaml
providers:
  - name: groq
    type: openai-compat
    base_url: https://api.groq.com/openai/v1
    api_key_env: GROQ_API_KEY
```

One `provider/openai` adapter serves all of them, one instance per upstream.
This is why N vendors do not mean N files.

**Tier 2 — a native adapter (the exception).** Only when a vendor genuinely does
not speak that format. `provider/vertex` is the one such case today, and its
package doc lists what forced it: the region is in the hostname and the model in
the path (so there is no single base URL to concatenate), auth is a short-lived
OAuth2 token rather than a static key, and the wire format is `contents/parts`
rather than `messages/choices`. Anthropic's native `/v1/messages` or Bedrock's
SigV4 signing would qualify; a vendor with a different base URL would not.

To add one: a new package under `provider/`, implement `provider.Provider` (4
methods: `Name`, `BuildRequest`, `TranslateResponse`, `TranslateStreamChunk`),
then add a case to `buildAdapter` in `internal/gateway/providers.go`. That
function is the only place that knows a vendor's name; `proxy.go` and the client
contract are untouched.

Either tier registers under the **config's** `name`, not the adapter type's own,
so two instances of one type can coexist and metrics and the circuit breaker key
on something the operator chose.

## Notes

- Vertex reports `thoughtsTokenCount` for thinking models. It is billed as output
  and has no OpenAI field, so it is folded into `completion_tokens`.
- The streaming path now accounts tokens; the pre-Vertex byte-pipe could not.

## Deferred

- Cross-replica dedup via Redis marker (extension point in
  `internal/gateway/dedup.go`).
- A per-write SSE deadline (`http.ResponseController.SetWriteDeadline`), so a hung
  stream reader is bounded without truncating healthy long streams. Until then
  `WriteTimeout` is deliberately unset — see `main.go`.
