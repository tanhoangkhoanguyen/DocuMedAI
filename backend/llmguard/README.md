# LLMGuard

An **OpenAI-API-compatible** gateway in front of the LLM provider.

```
client (base_url=…)  →  la-llmguard :8081  →  provider adapter  →  Vertex AI
                         ├ model allowlist (exact, per provider+model)
                         ├ admission control (in-flight ceiling, sheds 429)
                         ├ rate limit (Redis token bucket)
                         ├ retry + backoff (honors Retry-After)
                         ├ circuit breaker (one per provider, shared across replicas)
                         └ Prometheus /metrics
```

Clients speak the OpenAI wire format. Internally a **provider adapter** translates
to the vendor's native API. There are two, and the split matters when you add a
vendor: `provider/openai` is generic and serves every OpenAI-compatible upstream
by configuration alone, while `provider/vertex` is a native adapter for the one
API that cannot be reached that way. Core (rate limit, breaker, retry,
metrics) never sees vendor JSON, so adding a provider doesn't change clients or
core — see [Adding a provider](#adding-a-provider).

Only chat completions pass through. Embeddings and the reranker run locally in the
backend and never reach LLMGuard.

**Tool calling is out of scope.** The gateway proxies user→model completions only,
so a request carrying `tools`, `tool_choice` or a `role:"tool"` message is refused
with a 400. Refused rather than ignored on purpose: the fields are not modelled,
and `encoding/json` drops what it cannot model — so passing such a request through
would answer a function-calling caller with prose and no indication why.

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
| `RATE_LIMIT_RPM` / `RATE_LIMIT_BURST` / `RATE_WAIT_MAX` | `480` / `60` / `2s` | Token bucket. `RATE_WAIT_MAX` is queue depth, not latency saved — lowering it turns a slow success into a 429 |
| `RETRY_MAX` / `RETRY_BASE_DELAY` / `RETRY_MAX_DELAY` | `4` / `300ms` / `8s` | Backoff |
| `CIRCUIT_MIN_REQUESTS` / `CIRCUIT_FAIL_RATIO` / `CIRCUIT_OPEN_FOR` | `10` / `0.6` / `20s` | Breaker. `CIRCUIT_OPEN_FOR` is also the TTL of the cross-replica open flag |
| `MAX_IN_FLIGHT` | `256` | Concurrency ceiling — see below. `0` disables it |
| `UPSTREAM_TIMEOUT` / `MAX_IDLE_CONNS` | `120s` / `100` | HTTP client. `UPSTREAM_TIMEOUT` bounds the **buffered** path only — see below |
| `SERVER_IDLE_TIMEOUT` | `120s` | Idle keep-alive connections. There is deliberately no write timeout — see `main.go` |
| `STREAM_WRITE_IDLE` / `STREAM_IDLE_TIMEOUT` / `STREAM_ABSOLUTE_MAX` | `30s` / `60s` / `30m` | Streaming deadlines — see below. `0` disables each |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | **Empty disables tracing entirely.** OTLP/HTTP collector base URL, e.g. `http://la-otel-collector:4318` — see below |
| `OTEL_SERVICE_NAME` / `OTEL_TRACES_SAMPLER_ARG` / `OTEL_SHUTDOWN_GRACE` | `llmguard` / `1.0` / `5s` | Service name, head-sampling ratio, span-flush budget at shutdown |

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

### Streaming deadlines

A streaming request holds its admission slot for the whole life of the stream, so whatever bounds
that lifetime is what bounds the ceiling. A timeout on **total duration** cannot do it: a healthy
generation and a hung one both run long, and only the gap between events tells them apart. What is
bounded instead is **inactivity** — time since the last byte moved.

| Knob | Default | Bounds | Closes |
|---|---|---|---|
| `STREAM_WRITE_IDLE` | `30s` | Time since the last flush **to the client** | A client that opens a stream and stops reading. The send buffer fills, `Flush()` blocks, and `r.Context()` never fires because the client is silent rather than gone |
| `STREAM_IDLE_TIMEOUT` | `60s` | Gap between two frames **from upstream** | A provider that goes quiet mid-stream without erroring or closing |
| `STREAM_ABSOLUTE_MAX` | `30m` | Total stream duration | Backstop only. It should never fire; it exists because the two above can fail together |

Both inactivity bounds are **refreshed on every frame**, so a stream that keeps moving survives
indefinitely — a deadline of the same size applied absolutely would cut exactly the long generations
streaming exists for. `UPSTREAM_TIMEOUT` is *not* one of these: it is `http.Client.Timeout`, an
absolute deadline covering the body read, so the streaming path uses its own client. A single client
for both truncated every healthy stream past 120s and reported it as an upstream failure.

`STREAM_ABSOLUTE_MAX` is worth its own note. It only fires once the inactivity bounds have failed to,
which makes it the reading that says *the protection itself is broken* — either `SetWriteDeadline`
degraded to a no-op behind a `ResponseWriter` wrapper, or an upstream is dribbling frames just fast
enough to keep resetting the watchdog.

Aborts are counted by `llmguard_stream_aborts_total{reason}` — `upstream_idle`, `write_idle`,
`absolute_max`. The counter is needed because an aborted stream is otherwise **invisible**: the
header left with the first frame, so `requests_total` already recorded a 2xx, and the failure reaches
the client as an in-band SSE frame that no server-side counter sees.

### Tracing

Prometheus counts what happened; it cannot say why **one** request took 8s. A gateway is asked that
constantly, and the answer is a per-request timing tree:

```
POST /v1/chat/completions          8.2s  503
├── upstream.attempt  #0           3.0s  ✗ 503
├── upstream.attempt  #1           0.1s  ✗ 429
└── upstream.attempt  #2           4.7s  ✗ 503
```

That request was slow because it retried three times — not because the provider is slow. No counter
distinguishes those two.

**Off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set**, and off means nothing is installed: the global
tracer stays OpenTelemetry's no-op. Two measured notes behind that shape. A no-op span costs ~34ns
and one allocation against a request that spends *seconds* in an LLM call, so there is no
`if enabled` guard anywhere. And installing the SDK with a sample ratio of `0` is **not** the cheap
way to switch tracing off — the SDK builds a span before the sampler drops it, ~17× the no-op cost.
Leave the endpoint empty.

The endpoint is read by the SDK itself, which appends `/v1/traces`; the sibling
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is used verbatim. Inbound W3C `traceparent` headers are
adopted, so a call from the Python backend and the upstream call it triggers appear in **one** trace.

| Span / attribute | Says |
|---|---|
| `POST /v1/chat/completions` | Root span, created by `otelhttp`. Carries the refusal attributes below |
| `upstream.attempt` | One child **per attempt**, with `llmguard.retry.attempt` (0-based) and the upstream status. Four siblings is a retry storm; one slow span is a slow provider |
| `stream` | One child per SSE stream, with `llmguard.stream.frames`. Events `upstream_headers` then `first_frame` — the gap between them is the provider thinking, and `first_frame` is **time to first token** |
| `llmguard.refused_by` | Which protection refused: `admission`, `quota`, `breaker_remote`, `breaker_local`, `upstream` |
| `llmguard.stream.abort_reason` | Which deadline cut a stream, same vocabulary as `llmguard_stream_aborts_total` |

`refused_by` exists because **two pairs of refusals share a status code**: admission control and the
rate limiter both return 429, the local and cross-replica breakers both return 503. A 429 meaning
"the gateway is out of capacity" and one meaning "this caller is over quota" call for opposite
responses, and on the wire they are identical. `shed_total` vs `rate_limited_total` separate them in
aggregate, but a counter cannot say which *one* request was refused, or why.

There is deliberately **no span per SSE frame** and none for provider translation. A frame span would
mirror `upstream.attempt`, and streams reach tens of thousands of frames at ~780 bytes of span data
each — the same exhaustion vector `maxUpstreamBody` exists to prevent. Translation is an in-memory
unmarshal of a few µs, which a ~1.7µs span would cost as much to observe as to perform.

### Trace store (ClickHouse)

Spans go to an OTel Collector, which writes them to ClickHouse. The gateway names no backend — it
exports OTLP — so swapping the store is a change in `observability/otel-collector.yaml`, not in Go.

```bash
docker compose --profile observability up -d la-clickhouse la-otel-collector
OTEL_EXPORTER_OTLP_ENDPOINT=http://la-otel-collector:4318 docker compose up -d la-llmguard
```

Why a store at all: **durations land exactly**, so a quantile is computed rather than interpolated
between pre-declared histogram bounds. That is what a benchmark needs — a p95 read off a 3-second
bucket is worth ±1s, which hides any change smaller than the bucket.

```sql
-- p50/p95/p99 per provider, exact, over every attempt
SELECT SpanAttributes['llmguard.provider'] AS provider,
       count() AS attempts,
       quantileExact(0.50)(Duration)/1e6 AS p50_ms,
       quantileExact(0.95)(Duration)/1e6 AS p95_ms,
       quantileExact(0.99)(Duration)/1e6 AS p99_ms
FROM otel.otel_traces
WHERE SpanName = 'upstream.attempt'
GROUP BY provider;
```

Reach it with `docker exec la-clickhouse-service clickhouse-client -d otel`, or over HTTP on 8123.

**Check for dropped spans before trusting a benchmark.** Export is asynchronous and bounded at three
points — the SDK's batch queue, the collector's sending queue, ClickHouse itself — and an overflow at
any of them drops spans **silently**: no error, no log, no failed request. Prometheus is the control,
because `requests_total` is incremented in-process and cannot be lost:

```
requests_total (Prometheus)  ==  count(DISTINCT TraceId) (ClickHouse)
```

A shortfall means spans were dropped; raise `send_batch_size` / the sending queue in
`otel-collector.yaml`. Those values are deliberately left at their defaults until a real benchmark
says what the load is — guessing now would just be a different wrong number.

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
| `internal/gateway/proxy.go` | Admit → rate limit → breaker → retry; SSE translation loop |
| `internal/gateway/admission.go` | In-flight ceiling (counting semaphore) + shedding |
| `internal/gateway/ratelimit.go` | Redis token bucket (atomic Lua) |
| `internal/gateway/tracing.go` | Tracer provider setup + span/attribute vocabulary |
| `internal/gateway/retry.go` | Backoff + jitter + Retry-After + per-provider circuit breakers |
| `internal/gateway/breakershare.go` | Propagates a breaker trip to other replicas via Redis |
| `internal/gateway/metrics.go` | Prometheus collectors |
| `provider/` | What every adapter shares: normalized schema, `Provider` interface + registry, error vocabulary |
| `provider/openai/` | The **generic** adapter — every OpenAI-compatible upstream, config-only |
| `provider/vertex/` | The **native** adapter for Vertex AI `generateContent` (+ golden fixtures) |
| `mockupstream/` | Deterministic fake provider used by every test (and the benchmark) |
| `internal/testutil/redis.go` | Live-Redis gate for the Lua token-bucket tests |
| `internal/testutil/polling.go` | `Eventually` — waits on state that settles asynchronously |
| `internal/testutil/metrics.go` | Prometheus readers, so tests can assert on instrumentation |
| `observability/otel-collector.yaml` | OTLP in, ClickHouse out — the only place the trace store is named |
| `observability/clickhouse-user.xml` | The collector's ClickHouse user (the image's `default` is localhost-only) |
| `observability/clickhouse-init.sql` | Creates the `otel` database; the collector creates its own tables |

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

## Multi-replica

Breaker state crosses replicas: when a local breaker opens it publishes
`llmguard:breaker:open:<provider>` with TTL `CIRCUIT_OPEN_FOR`, and the others fail
fast on that instead of each collecting `CIRCUIT_MIN_REQUESTS` failures of their own
against an upstream already known to be down.

What crosses is the **trip signal, not the counters**: sharing counters would put a
Redis round trip on every request, while a trip is one fact with a natural lifetime
and needs no consensus. Only the *positive* reading is cached — remembering "healthy"
would delay a replica's entry into an outage, which is the lateness the flag exists to
remove. Any Redis error fails open, so a Redis outage can never shed traffic by itself.

Recovery stays local: nothing clears the flag early, and each replica's own half-open
probe decides when it trusts the upstream again.

### Running it

A `multi-replica` compose profile stands the whole thing up — nginx in front of N
replicas, one shared Redis, and the deterministic mock upstream, so it needs no GCP
project and no spend:

```bash
docker compose --profile multi-replica up -d   la-redis la-mockupstream la-llmguard-replica la-nginx
curl localhost:8082/healthz                      # through the load balancer
docker compose stop la-nginx la-llmguard-replica la-mockupstream

# scale beyond the default 2
docker compose --profile multi-replica up -d --scale la-llmguard-replica=3   la-redis la-mockupstream la-llmguard-replica la-nginx
```

Both commands name their services on purpose. `up` with no arguments also starts
every **profile-less** service — the Python backend, the MCP server, the frontend —
a full application build nobody wants just to exercise the load balancer. And `stop`
rather than `down`, because **`down` ignores profiles** and would tear down the
shared `la-redis` and `la-mongo` with the rest of the project.

nginx listens on **8082**, not 8081, so the profile runs *alongside* the default
stack. The replicas use `config.multi-replica.yaml`, which routes to the mock; the
committed `config.yaml` is untouched. `nginx/nginx.conf` documents the three settings
that are not optional — `proxy_buffering off` above all, since without it
time-to-first-token silently becomes full completion latency.

### Proving a replica can die

`bench/killreplica` opens 12 concurrent SSE streams against a 3-replica stack,
SIGKILLs one replica mid-delivery, and asserts what the fleet did:

```bash
go run ./bench/killreplica              # enforce the threshold
go run ./bench/killreplica -threshold 0 # measure only, never fail
```

Streams pinned to the dying replica **do** drop: nginx can retry only before it has
forwarded a response header, and the SSE header leaves within milliseconds. So drops
are reported rather than failed on, and the assertions are fleet-level — every
*other* stream runs to full length, a post-kill wave still succeeds, and a survivor's
own `/metrics` shows it absorbed the work.

Measured over four runs: **4 of 12 streams dropped** (the victim's third, severed at
frames 8–11 of 27) and **84 of 84 post-kill streams succeeded**. The threshold is
0.90 rather than that measured 1.0 — the property is "the fleet still serves", not
"nothing ever retries", and losing a third of capacity without nginx's retry would
land near 67%.

### Two caveats

**The rate limit aggregates; the concurrency ceiling does not.** The token bucket is
in Redis, so N replicas share one limit — `TestRateLimitSharedAcrossReplicas` pins
it. `MAX_IN_FLIGHT` is a per-process semaphore, so effective concurrency is **N ×
256**. Adding a replica does not raise the request rate and does raise how many
requests run at once; size it per replica.

**Prometheus must scrape replicas directly.** Each keeps its own registry, so a
scrape through nginx round-robins between replicas and returns different counters
each time. Point it at the replica tasks and aggregate with `sum()`.

## Deferred

- In-flight **de-duplication**. A `singleflight` deduper was removed: it only
  coalesced byte-identical *concurrent* bodies, which real chat traffic almost
  never produces (the history plus the new turn differ per request), so it cost a
  dependency, a metric and a test file for close to nothing.
