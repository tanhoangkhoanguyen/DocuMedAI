# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DocuMedAI is a full-stack AI-powered medical document analysis system. Users upload medical documents, then ask questions via a chat interface. The backend uses a multi-agent LangGraph workflow with RAG retrieval across vector databases. The system supports persistent conversation memory, Redis caching, and JWT + Supabase authentication.

**Key concept**: The LangGraph `StateGraph` is a **strictly linear pipeline** (no conditional edges — see `workflow.py:88-93`): TopicChecker → MessageAnalysis → LongTermMemoryRetriever → Agents (CrewAI) → SchemaUpdater. The branching (topic-change early-return, revision loop) lives *inside* the nodes, not in graph routing. RAG uses Qdrant retrieval with cross-encoder reranking. When a topic change is detected inside `TopicChecker`, the prior conversation is summarized and archived to a Qdrant long-term memory collection.

## Architecture

| Layer | Tech | Location |
|-------|------|----------|
| Frontend | Next.js 15 / React 19 / TypeScript | `frontend/` |
| Backend API | FastAPI (port 2010) | `backend/services/app/` |
| Agent workflow | LangGraph StateGraph | `backend/services/chatbot/` |
| Multi-agent | CrewAI | `nodes.py` — Agents node |
| RAG pipeline | Qdrant retrieval + BAAI reranker | `backend/utils/rag.py` |
| LLMGuard | Go gateway (admission control, rate limit, retry, circuit breaker); OpenAI-compatible API → provider adapter (`openai` generic / `vertex` native) → upstream. Chat completions only — `tools`/`tool_choice` are refused with a 400. Standalone; not in the app's request path | `backend/llmguard/` |
| Memory cache | Redis (TTL 1800s → flush to MongoDB) | `backend/utils/redis_client.py` |
| Persistence | MongoDB | `backend/utils/mongo_client.py` |
| Auth | JWT + Supabase SSR | `backend/services/app/auth_api.py`, `frontend/lib/supabase/` |
| Vector DB | Qdrant (default); alternatives benchmarked in `backend/vector_database_tests/` | `backend/toolcore/tools/` |
| Tool Core | In-memory tool registry + the single enforcement point (`MCPServer.call_tool`) | `backend/toolcore/core.py` |
| MCP server | Real MCP protocol (JSON-RPC 2.0 over streamable-HTTP, official `mcp` SDK) at `/mcp` on port 8090 | `backend/mcp_server/` |
| ID hashing | `pattern_cipher.py` does NOT encrypt — it's `uuid5` deterministic IDs + bcrypt helpers (the bcrypt helpers are currently unused; auth stores plaintext passwords). No message encryption exists anywhere. | `backend/utils/pattern_cipher.py` |

**Important**: `backend/services/app/` holds the FastAPI routes and workspace layer. `backend/services/chatbot/` holds the LangGraph graph, nodes, and tools. `backend/utils/` holds shared DB clients (MongoDB, Redis, Supabase), the RAG pipeline and `llm_config.py`.

**Tool Core, one core / two surfaces**: `toolcore/core.py` holds the three RAG tools
(`identity`, `search_medical_knowledge`, `search_user_documents`) and is reached two ways:
in-process by the LangGraph `Agents` node, and over real MCP by external hosts
(Claude Desktop, MCP Inspector) via `backend/mcp_server/`. Both go through
`MCPServer.call_tool(name, args, principal)` — raw dict args + a typed `Principal` →
`ToolResult`. Per-user isolation is enforced **once, there**: `search_user_documents` sets
`requires_principal = True` and `call_tool` refuses before validating or executing when
`principal is None`. `user_id` is threaded per-call, never bound to the cached singleton
server (`get_mcp_client`), so concurrent users can't see each other's documents.
`execute_tool_call(name, message, user_id)` is a legacy string-in/string-out shim over
`call_tool` kept so the internal planner in `nodes.py` is untouched; it swallows
`PrincipalRequiredError` into `""` (and logs a warning) while the MCP surface maps it to a
protocol error. When adding a tool, add a `ToolSpec` to `_BUILTIN_MCP_TOOLS` — both surfaces
pick it up, and `ci_tests/integration/mcp/test_cross_surface_equivalence.py` asserts they
return identical content.

**MCP auth**: The MCP boundary is the *only* auth surface — `source="internal"` callers
bypass it. An ASGI step in `mcp_server/auth.py` verifies `Authorization: Bearer <jwt>` via
the shared `services.app.auth_deps.decode_bearer_any` (local HS256 + Supabase) and binds a
`Principal(source="mcp")` for the request scope. Absent token → anonymous (the two unscoped
tools still work); invalid token → HTTP 401 before any tool runs.

**LLMGuard**: A standalone Go gateway (port 8081) that exposes an OpenAI-compatible `/v1/chat/completions` and translates it to a vendor's native API via a provider adapter. It is in the request path for **one node only**: `TopicChecker` builds its client with `build_chat_model` (`backend/utils/llm_config.py`), which returns a `ChatOpenAI` aimed at `LLM_GATEWAY_URL`. Every other node calls Vertex directly with `ChatVertexAI`, and `crewai.LLM` reaches Vertex through litellm's `vertex_ai/` prefix — because the gateway refuses tool calling, which is how LangChain implements `with_structured_output`. `LLM_GATEWAY_URL`/`LLM_GATEWAY_PROVIDER` are required (`:?` in compose): the backend will not start without them. Embeddings and the reranker run locally. Both LLMGuard and the app read the same `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` vars and authenticate via ADC. See `backend/llmguard/README.md`.

**Scope: user→model completions only.** LLMGuard does not proxy the model→tool half. `tools`, `tool_choice` and `role:"tool"` messages are **refused with a 400**, not ignored — the fields are absent from `provider.ChatRequest`, and since every adapter re-marshals that struct rather than forwarding raw bytes (`openai.go`'s `json.Marshal(&outbound)`, `vertex.go`'s `toNative`), `encoding/json` would silently drop them and hand a function-calling caller a prose answer with no indication why. The check is a **targeted probe** in `proxy.go` (`unsupportedToolField`), deliberately not `DisallowUnknownFields()`, which would also reject every other OpenAI field the schema does not model (`n`, `seed`, `presence_penalty`, `response_format`, `user`) — pinned by `TestUnmodelledOpenAIFieldsStillPass`. `role:"tool"` is checked separately from the raw-body probe because it survives decoding and would otherwise reach the Vertex adapter's `default:` branch and be reinterpreted as an ordinary user turn.

**LLMGuard providers, two tiers — read this before adding a vendor.** `provider/`
holds only what every adapter shares (the `Provider` interface, the registry, the
normalized schema in `schema.go`, the error vocabulary in `errors.go`). Each vendor's
translation lives in its own package, and there are exactly two because they are not
peers:

- **`provider/openai/`** — the **generic** adapter (`openai.New`, `openai.Client`).
  Serves *every* upstream that already speaks OpenAI's `/chat/completions`: OpenAI,
  OpenRouter, Groq, Together, DeepSeek, vLLM, and Gemini's own compat endpoint. Adding
  one of those is **four lines of `config.yaml` and no Go code** — N vendors do not mean
  N files. This is the default path.
- **`provider/vertex/`** — a **native** adapter (`vertex.New`, `vertex.Client`), the
  exception. Justified only because Vertex breaks the format three ways: region in the
  hostname + model in the path (no single base URL), short-lived OAuth2/ADC token
  instead of a static key, and `contents/parts` instead of `messages/choices`.

`config.yaml`'s `type:` (`vertex` | `openai-compat`) picks the adapter and has **no
default** — omitting it is a startup error, because it decides URL shape, auth and wire
format. `name:` is operator-chosen and is the registry key, the metrics label and the
per-provider circuit-breaker key; it defaults to the type's own name when blank. The
registry keys on `Provider.Name()`, so an adapter constructor **must** take that
configured name — a hardcoded constant makes every differently-named entry unroutable
and panics `Register` on a second instance (the bug fixed in `5c9f5f6`, pinned by
`TestRegisterRejectsDuplicateName`). Vertex auth is ADC only: the same binary reads a
JSON key file locally via `GOOGLE_APPLICATION_CREDENTIALS` and the attached service
account on GCP, with no code change and no `credentials_file` config field.

## Common Commands

### Docker (primary workflow)
```bash
docker compose up -d --build          # Start all services
docker compose down                   # Stop all services
docker compose logs -f la-documedai     # Tail backend logs
docker compose --profile vectordb-lab up -d  # Include optional vector DB lab services

docker compose up -d --build la-mcp-server   # MCP surface on http://localhost:8090/mcp
docker compose --profile observability up -d la-prometheus la-grafana
```
`la-mcp-server` reuses the `la-documedai` image with a different command (`python -m
mcp_server`), skipping `entrypoint.sh`. The app does **not** depend on it — the backend
stack runs identically whether it is up or not.

### Frontend development
```bash
cd frontend
npm install
npm run dev    # http://localhost:2011 (Turbopack)
npm run build  # Production build
npm run lint   # ESLint
```

### Backend development
```bash
pip install -r backend/requirements-prod.txt   # runtime deps
pip install -r backend/requirements-dev.txt    # + vector DB lab clients (milvus/weaviate/etc.)
python backend/services/app/run_app.py          # FastAPI server — http://localhost:2010
python backend/services/chatbot/run_chatbot.py  # Graph-only test harness (no HTTP)
```

### Tests
Tests live in `ci_tests/` and run against live services inside the `la-documedai` container
(see `pytest.ini`: `testpaths = ci_tests`, `pythonpath = backend .`). They are integration
tests, not unit tests — Mongo/Redis/Qdrant must be reachable.

```bash
# CI (matches .github/workflows/python-ci.yml) — three independent steps, each `if: always()`
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.ci.yml"
$COMPOSE up -d --build --wait la-qdrant la-mongo la-redis
$COMPOSE up -d --build la-documedai

# App tests
$COMPOSE exec -T la-documedai pytest -q ci_tests/integration/utils ci_tests/integration/api
# Vector DB tests
$COMPOSE exec -T la-documedai pytest -q ci_tests/integration/vector_db/test_qdrant_client.py
# MCP tests (tool core contracts/isolation/observability + real JSON-RPC conformance)
$COMPOSE exec -T la-documedai pytest -q ci_tests/integration/tool_core ci_tests/integration/mcp

# Single file inside the container
$COMPOSE exec -T la-documedai pytest -q ci_tests/integration/api/test_chat_api.py

$COMPOSE down -v
```

`ci_tests/integration/mcp/test_mcp_server.py` starts the real HTTP app on a loopback uvicorn
and drives the JSON-RPC lifecycle with the official MCP SDK **client** — so it exercises
transport, not a stub. A *successful* `search_user_documents` needs seeded Qdrant + LLM keys,
so that path is covered by the isolation tests in `ci_tests/integration/tool_core/` instead.

Markers (`pytest.ini`): `integration` (live services), `asyncio` (pytest-asyncio),
`mcp` (JSON-RPC lifecycle over streamable-HTTP). There is **no `vectordb` marker and no
`vectordb-lab` CI job** — the multi-engine lab (Milvus/Weaviate/Vespa/ChromaDB) is run
manually via the `vectordb-lab` *compose profile* and needs `requirements-dev.txt` clients.
The only Qdrant test CI runs is `ci_tests/integration/vector_db/test_qdrant_client.py`.

`docker-compose.ci.yml` overrides `la-documedai` to idle (`sleep infinity`, healthcheck
disabled) and mounts the repo at `/workspace`, so tests run via `exec` rather than the
prod entrypoint. The graph is mocked in `ci_tests/conftest.py` (`_build_graph` patched),
so backend API tests don't call OpenAI.

### LLMGuard (Go) tests

Run from `backend/llmguard/` — plain `go test`, no Docker, no provider credentials:

```bash
make test              # go test -race -count=1 ./...  (race auto-disabled when cgo is off, e.g. Windows)
make lint              # go vet + pinned golangci-lint (auto-installs via `make tools`)
make bench             # benchmarks only, with -benchmem
go test -run TestRetry ./...              # single test
go test -run TestX -count=1 .             # single test, root package only
make run-mock          # standalone mockupstream on :8090
```

**Self-protection vs upstream-protection.** Retry and the breaker shield the *upstream* from
LLMGuard. `admission.go` shields *LLMGuard from its callers*, and it bounds a different quantity than
the rate limiter: `RATE_LIMIT_RPM` caps arrival **rate**, `MAX_IN_FLIGHT` (256) caps **concurrency**.
Concurrency is what maps to memory — each in-flight request holds a goroutine, a buffer up to 10 MiB
and an upstream connection — and the two diverge when upstream slows, because the same admitted RPM
then yields far more concurrent requests. Acquiring is non-blocking (a queued request still holds what
the ceiling bounds) and a shed is **429 + `Retry-After`**, not 503, since the upstream is healthy and
the gateway is full. It sits before the rate limiter (which can block for `RateWaitMax`) and after
`provider.For` (so a shed request names a real route). `llmguard_shed_total` is separate from
`llmguard_rate_limited_total`: both are 429s, but one is a caller over quota and the other an operator
capacity problem.

**The breaker keys on the route, and 429 does not trip it.** Both were per provider and
`isRetryable`, and both were wrong for the same reason: they conflated "this upstream is sick" with
something narrower. One bad model took every healthy model on that upstream with it, and a spent
quota opened a circuit that then found the quota still spent — a self-inflicted outage the breaker
could not probe its way out of. So `tripsBreaker` is 5xx-and-transport-only (`isRetryable` still
retries 429), and `llmguard_circuit_state` carries `provider` **and** `model`. Quota is the rate
limiter's job, budgeted per route in `config.yaml`.

**Breaker state is cross-replica.** `breakershare.go` publishes
`llmguard:breaker:open:<provider>:<model>` (TTL `CIRCUIT_OPEN_FOR`) when a local breaker opens, so N replicas
don't each burn `CIRCUIT_MIN_REQUESTS` failures learning the same outage. What crosses is the **trip
signal, not the counters** — sharing counters would put Redis on every request's hot path. Only the
**positive** reading is cached: caching "healthy" would delay a replica's entry into an outage, which
is exactly the lateness the flag removes. Redis errors fail open (same posture as `ratelimit.go`), and
nothing clears the flag early — recovery is each replica's own half-open probe. A `nil *BreakerSharer`
is a working no-op, which is how single-replica runs and the whole test suite avoid needing Redis.
The check runs **after** admission and the rate limiter: it costs a Redis `EXISTS`, so it is not paid
until the request is known to have both capacity and a token.

**A stream is bounded by inactivity, never by total duration.** A healthy generation and a hung one
both run long; only the gap between events separates them. So `UPSTREAM_TIMEOUT` (an absolute
`http.Client.Timeout` covering the body read) bounds the **buffered path only** — streaming has its
own client over the *same transport*, because one client for both truncated every healthy stream past
120s and reported it as an upstream failure. Two inactivity bounds replace it, each **refreshed per
frame**: `idlewatchdog.go` cancels the upstream read after `STREAM_IDLE_TIMEOUT` of silence between
frames, and `writedeadline.go` applies a socket write deadline of `STREAM_WRITE_IDLE` for a client
that stops reading (`r.Context()` cannot catch that — such a client is silent, not gone).
`STREAM_ABSOLUTE_MAX` (30m) is a backstop behind both; because it only fires once they have failed
to, a non-zero reading means the protection itself is broken. `llmguard_stream_aborts_total{reason}`
names all three, and is needed because an aborted stream is otherwise invisible: the header left with
the first frame, so `requests_total` already recorded a 2xx. Two subtleties worth keeping: the
`abortReason` check runs **before** `scanner.Err()`, since a cancelled read can surface as a clean EOF
and would otherwise emit `[DONE]` on a truncated stream; and only a deadline error counts as
`write_idle`, since a client closing the connection also fails the write and is ordinary traffic.
There is deliberately **no clear()** of the write deadline — `net/http` already resets it after every
handler returns.

**Tracing answers “why was THIS request slow”, which no counter can.** Off unless
`OTEL_EXPORTER_OTLP_ENDPOINT` is set, and off means **nothing is installed** — the global tracer
stays OpenTelemetry's no-op. That is the whole switch, and two measurements decided its shape (both
in `make bench` — `BenchmarkSpanNoop`, `BenchmarkSpanSDKSampleZero`): a no-op span is **~450ns and 4
allocations** against a request that spends *seconds* in an LLM call, so there is no `if enabled`
guard at any of the ~10 call sites; and installing the SDK with sample ratio `0` costs **~2.3×** the
no-op path, because the SDK builds a recording span before the sampler drops it — so “ratio 0” is
the wrong way to disable. `otelhttp` wraps **only** `/v1/chat/completions` (a 10s
healthcheck and a Prometheus scrape would bury real traffic) and is what adopts an inbound
`traceparent`, putting the Python backend's call and the upstream call in **one** trace. Wrapping was
the real risk, not the spans: `serveStreaming` needs `http.Flusher` and a working
`SetWriteDeadline`, and a `ResponseWriter` wrapper without `Unwrap()` silently disarms the
stalled-reader bound — invisible to every other streaming test, since they use a recorder with no
connection. `TestWriteDeadlineSurvivesOtelHandler` pins it. The span tree is deliberately shallow:
one `upstream.attempt` **per retry attempt** (four red siblings is a retry storm; one slow span is a
slow provider) and one `stream` per SSE stream carrying `frames` plus a `first_frame` event —
**time to first token**, which exists nowhere else here because a stream's total duration is
dominated by how *long* the answer is. There is **no span per frame** (streams reach tens of
thousands of frames at ~780 bytes each — the exhaustion vector `maxUpstreamBody` exists to prevent)
and none for provider translation (a few-µs unmarshal observed by a ~1.7µs span). Two attributes
carry what status codes cannot: `llmguard.refused_by`, because admission-vs-quota both return **429**
and local-vs-remote breaker both return **503**, and `llmguard.stream.abort_reason`, reusing the value
`streamAbortReason` already computed for the counter so span and metric cannot disagree. One
subtlety worth keeping: `endStream` runs from a **defer**, because the pre-header error branch returns
early twice to avoid double-recording latency — a tail call is skipped on exactly those paths and
leaks a span that is never exported.

**Prometheus keeps state and refusals; ClickHouse took latency.** The split is not about which store is nicer, it is about what each can hold. A gauge has no row to insert — `circuit_state` is the *absence* of requests and `in_flight` is a live count — so those cannot move, and `in_flight` is the only signal that predicts shedding *before* `shed_total` moves. `requests_total` stays as the denominator of every ratio and as the control on dropped spans. `shed_total` and `rate_limited_total` stay because both return **429** and nothing else separates a gateway out of capacity from a caller over quota; `stream_aborts_total` stays for the same reason — an aborted stream already counted as a 2xx when the header left, so the counter is the only server-side witness, and unlike a span it cannot be dropped. What left is what the trace store answers better: `request_duration_seconds` (`quantileExact` over exact durations, no bucket to interpolate), `retries_total` (`GROUP BY TraceId` gives the distribution, not just a total) and `tokens_total` (deliberately not replaced — LLMGuard is a reliability gateway and had no consumer for it).

**Spans land in ClickHouse, not a trace UI.** An OTel Collector (`llmguard/observability/otel-collector.yaml`) receives OTLP and writes `otel.otel_traces`, so the Go code names no backend and swapping stores is a config change. The reason is the benchmark: `quantileExact` over exact nanosecond durations replaces a quantile interpolated between pre-declared histogram bounds, where a p95 read off a 3-second bucket hides any change smaller than the bucket. What this costs is that export is asynchronous and bounded at three points — the SDK batch queue, the collector sending queue, ClickHouse itself — and an overflow at any of them drops spans **silently**. `requests_total` is the control, being incremented in-process: it must equal `count(DISTINCT TraceId)`, and a shortfall means the numbers are incomplete.

The pipeline lives in `internal/gateway/` (config, proxy, admission, retry, breakershare,
ratelimit, metrics, idlewatchdog, writedeadline, tracing);
`main.go` at the module root is a thin composition root, and `internal/gateway/gateway.go` is the
entire exported surface between them. Everything else in the package stays unexported, and tests
sit beside the code they exercise so nothing is exported merely to be testable.

The suite is **characterization tests**, each sitting beside the source file it exercises
(`retry_test.go`, `ratelimit_test.go`, `circuitbreaker_test.go`; `proxy.go`'s
larger surface is split into `proxy_buffered_test.go`, `proxy_streaming_test.go`,
`proxy_errors_test.go`, plus `usage_test.go`) and sharing a harness in `harness_test.go`.
`admission_test.go`, `breakershare_test.go`, `proxy_streaming_deadline_test.go` and
`tracing_test.go` are the
**exceptions** to the characterization rule: that behavior is new, so there is no prior conduct to
preserve and the tests state the intended contract (a shed is 429 + `Retry-After`, counted apart from
quota 429s, and a slot always comes back; only the positive breaker reading is cached, and a Redis
error never sheds; a stream is cut by inactivity but never by total duration, and what must come back
is the **admission slot** — every test there asserts on `llmguard_in_flight`, because a handler that
returns while its goroutine still holds a slot passes every status assertion and still ratchets the
gateway to zero capacity). The stalled-reader test dials a **real socket**: `httptest.ResponseRecorder`
is an in-memory buffer that never blocks, so that leak is invisible to the rest of the suite. `tracing_test.go` swaps the **process-wide** tracer provider and propagator per test, so nothing there may call `t.Parallel()`; it drives requests through `otelhttp` rather than `h.do`, because a span the handler never created cannot be asserted on, and it finds the root span by NAME rather than by "has no parent" — warm-up requests through `h.do` are unwrapped, so the children they leave behind are parentless too. `config_test.go` guards
the harness/production invariant and **fails when a new `Config` field is added** without a decision
about whether `realDefaults()` mirrors it — by design, not an obstacle. `mockupstream/` has its own
tests pinning the determinism the Phase 6 benchmark depends on. Two rules matter when editing them:

- They pin what the proxy does **today**, not what it should do. Surprising behavior is locked
  in as-is with a `QUIRK` comment. Don't "fix" a test to encode intended behavior — that would
  let a refactor silently change actual behavior.
- Thresholds come from the real `loadConfig()` defaults (`RetryMax=4`, `CircuitMinReqs=10`,
  `CircuitFailRatio=0.6`, `RateLimitBurst=60`). Only the retry *delay* knobs are shortened.

Under `provider/`, tests follow the package split: `provider_test.go` covers the registry
(using a local `stubProvider` — the real adapters live in child packages that import the
parent, so using one there would be a circular import) and `schema_test.go` pins the
normalized wire shape. `provider/vertex/` owns the **golden fixtures** in `testdata/` (driven by
`vertex_golden_test.go`),
compared byte-for-byte with deliberately **no `-update` flag** — a regenerable golden turns
"the bytes changed" into one command that re-blesses whatever the code now does. Edit a
fixture by hand and justify it in review. `.gitattributes` pins
`backend/llmguard/provider/vertex/testdata/**` to LF; **move that path if the fixtures ever
move**, or a Windows checkout rewrites them to CRLF and the golden tests break.

Failure modes are driven through `mockupstream`'s knobs (error rate, status, latency, outage
window) over a real loopback socket rather than hand-written responses. `mockupstream` is a
**package** with a thin `cmd/` binary so the in-process fake
(`httptest.NewServer(mockupstream.New(...))`) is byte-identical to the standalone process.
Responses are deterministic: content is a pure function of the request, chaos is seeded per
request from the request bytes (not a package-level RNG), and content/chaos draw from separately
seeded streams. Identical request bodies therefore share one error-rate verdict — set
`X-Mock-Nonce` (or `X-Request-Id`) to vary it. See `mockupstream/README.md`.

Redis-backed tests (the rate limiter's Lua token bucket) use **DB 15** via
`internal/testutil.RequireRedis`, and **skip** rather than fail when Redis is unreachable, so
`make test` stays green on a laptop. Override with `TEST_REDIS_URL`.

## Service Ports

| Service | Port |
|---------|------|
| Backend API (FastAPI) | 2010 |
| Frontend (Next.js) | 2011 |
| Qdrant | 6333 |
| MongoDB | 27017 |
| Redis | 6379 |
| LLMGuard (Go) | 8081 |
| MCP server | 8090 (`observability` scrape target; always available) |
| Prometheus | 9090 (`observability` profile) |
| Grafana | 3000 (`observability` profile) |
| OTel Collector | 4318 OTLP/HTTP (`observability` profile) |
| ClickHouse | 8123 HTTP, 9000 native (`observability` profile) |

Swagger UI: `http://localhost:2010/docs`
LLMGuard metrics: `http://localhost:8081/metrics`
Traces: `docker exec la-clickhouse-service clickhouse-client -d otel` (`observability`
profile; tracing is off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set)
MCP endpoint: `http://localhost:8090/mcp/` · metrics: `http://localhost:8090/metrics/`
(both are `Mount`s — the **trailing slash matters**, the slashless form 307-redirects)

## Key Files

| File | Purpose |
|------|---------|
| `backend/services/app/run_app.py` | FastAPI entry point; graph config constants live here |
| `backend/services/app/chat_api.py` | Chat endpoints; drives the graph via the workspace layer (synchronous `graph.invoke` run in a threadpool — there is no `ChatGraph` class and no `ainvoke`) |
| `backend/services/app/chatbot_workspace.py` | Redis/Mongo workspace layer; manages TTL flush |
| `backend/services/app/auth_api.py` | Auth endpoints + JWT dependency injection |
| `backend/services/chatbot/workflow.py` | Builds and compiles the LangGraph StateGraph |
| `backend/services/chatbot/nodes.py` | All 5 graph node implementations |
| `backend/utils/rag.py` | RAG pipeline (paraphrase → retrieve → rerank → threshold) |
| `backend/services/chatbot/constants/schemas.py` | Pydantic models: `GraphState`, `ChatMessageState`, etc. |
| `backend/services/chatbot/constants/prompts.py` | All LLM prompt templates |
| `frontend/components/ChatLayout.tsx` | Main chat UI; hand-rolled SSE parser for the streaming endpoint |
| `frontend/lib/proxy-to-backend.ts` | Frontend → backend API client (buffered HTTP; `internal-api.ts` under `frontend/lib/utils/` is just an env-var reader) |
| `backend/toolcore/core.py` | Tool registry + `call_tool` — the single isolation enforcement point for both surfaces |
| `backend/mcp_server/__main__.py` | MCP ASGI app: per-request bearer auth → `StreamableHTTPSessionManager`, `/mcp` + `/metrics` |
| `backend/mcp_server/README.md` | MCP runbook + measured overhead/capacity benchmarks |
| `docs/benchmarks/mcp-procedure.md` | Two-VM GCP setup for the serving-capacity load test |

## Docs

`docs/` holds what spans packages — `deployment.md`, `benchmarks/` (results plus
the runbooks that reproduce them), `roadmap/` (per-subsystem plans, kept as
plans). A doc describing one package stays beside that package's code instead,
so it is edited in the same diff. See `docs/README.md`.

## Graph Config (run_app.py)

```python
chat_model = "gemini-2.5-flash"  # gpt-4o-mini in older commits; now Gemini via the Go proxy
embedding_model = "sentence-transformers/all-MiniLM-L6-v2"  # dim 384
reranking_model = "BAAI/bge-reranker-v2-m3"
qdrant_threshold = 0.25
reranking_threshold = -5  # raw cross-encoder logit; effectively "accept almost anything" — not calibrated
topic_threshold = -5      # same caveat
shortterm_memory_size = 5
max_revision_cycles = 1   # draft→critic→revise cycles
max_workers = 4  # requires ≥8 CPU cores
```

## Required Environment Variables

Copy `.env.example` to `.env` in the project root — it is the authority, grouped
by what reads each var (app / auth / LLMGuard / benchmark-only). Two things worth
knowing before editing it:

- `la-llmguard` and its replicas load the **whole** `.env` via `env_file`, so any
  var there reaches the gateway even when `docker-compose.yml` never names it.
  That is how `RATE_LIMIT_RPM` and `MAX_IN_FLIGHT` are set.
- The benchmark-only block stays commented for normal runs. `MOCK_LATENCY` makes
  the mock upstream slow on purpose, and a raised `RATE_LIMIT_RPM` disables the
  limiter — both silently change what any other measurement means.

## Data Flow: Chat Message

1. Frontend posts to `/chats/{chat_id}/messages` (Next.js API route → backend)
2. `chat_api.py` extracts JWT, loads the workspace, runs the graph synchronously (`graph.invoke` in a threadpool)
3. LangGraph (linear): `TopicChecker` → `MessageAnalysis` → `LongTermMemoryRetriever` → `Agents` → `SchemaUpdater`
4. `Agents` node runs CrewAI (Draft Writer → Critic reflection loop) with the RAG tool (Qdrant retrieval, cross-encoder reranker)
5. Response stored in Redis; flushed to MongoDB on TTL expiry, explicit save, or graceful shutdown (`flush_all`)
6. **"Streaming" is cosmetic**: the reply is computed in full, then the SSE endpoint (`/messages/stream`) regex-chunks the finished string into fake `token` events (`chatbot_workspace.py:619-630`). Time-to-first-token equals full latency; there is no real token streaming through the graph.

## Data Flow: Document Upload

1. PDFs processed by `backend/vector_database_tests/data_processing.py`
2. Uploaded via `data_uploading.py` to Qdrant (and optionally other vector DBs)
3. Indexed with `all-MiniLM-L6-v2` embeddings (dim 384)

## Vector DB Benchmark Lab (`backend/vector_database_tests/`)

A benchmark to choose which engine to self-host (Qdrant default vs Milvus,
Weaviate, Vespa, ChromaDB). The benchmark *pipeline* is a decision tool, but note this
directory is **load-bearing for prod**: the running app imports its Qdrant client
(`from vector_database_tests.utils.qdrant_client import get_qdrant_client` in
`chatbot_workspace.py:11`). Treat `vector_database_tests/utils/qdrant_client.py` as
production code, not throwaway benchmark code.
Methodology follows [ann-benchmarks](https://github.com/erikbern/ann-benchmarks): latency is
only comparable **at equal recall**, so a fast-looking engine isn't rewarded for silently
searching fewer candidates.

Pipeline (each step selects the engine via `BENCH_DB` env var / `--db`, dispatched through
`utils/registry.py`; each writes a per-DB JSON so results record which engine produced them):

| Stage | File | What it does |
|-------|------|--------------|
| Ground truth | `ground_truth.py` | Exact-kNN (faiss `IndexFlatIP`, cosine) over the full corpus, **once** — independent of any DB |
| Index | `data_uploading.py` | Times indexing; Milvus index build is inside the timed region |
| Recall + serial latency | `recall.py` | One serial pass: recall@k vs ground truth + median/p95 latency |
| Equal-recall sweep | `sweep.py` | Sweeps each engine's query-effort knob (HNSW `ef` / Vespa `targetHits`), picks lowest-latency config with recall@10 ≥ 0.95 |
| Throughput | `throughput.py` | **Open-loop fixed-QPS** driver (replaced Locust — closed-loop hides tail latency / coordinated omission). Measures latency from scheduled send time |

Fairness invariants every engine must share: identical build-side HNSW params (`M=64`,
`efConstruction=200`), equal Docker budgets (`mem_limit: 8g`, `cpus: 4.0`), `TOP_K=50`, and a
**shuffled query order** with discarded warmup (`registry.shuffled_order`, fixed seed) so no
result cache can bias latency. The shuffle preserves `query_id`, so recall is unaffected.

Each `utils/*_client.py` exposes `retrieve_ids(collection, vec, top_k, search_param)` returning
stable ids for recall. **Weaviate is pinned to client v3** (its `ef` is class-level, so it can't
be swept per-query like the others — a documented limitation; v4 migration would lift it).
Pinecone was removed (its `pinecone-local` emulator can't be made fair). See the lab's own
`README.md` for the full runbook.

## CI

GitHub Actions (`.github/workflows/`) — three workflows: `python-ci.yml`, `frontend-ci.yml`
(`npm run lint` + `npm run build`) and `llmguard-ci.yml`. `python-ci.yml` has a single
`backend` job that runs three independent `pytest` steps (App / Vector DB / MCP), each
`if: always()` so one failure doesn't mask the others. Both are triggered on PRs and pushes to
`main`/`develop`/`feature/Setup-CI`. CI writes a throwaway `.env` with test secrets and sets
`SKIP_MODEL_PRECACHE=1`; the backend graph is mocked, so no LLM API keys are needed.

`llmguard-ci.yml` gates the Go service (`make test` + `make lint`, plus a `go mod tidy`
check) with a `redis:7-alpine` service container on DB 15. It is **path-filtered** to
`backend/llmguard/**`, so it stays off the critical path for the other two.

## Cursor Rules (`.cursor/rules/`)

The repo carries Cursor rules that apply to work here regardless of which assistant is running.

`agents-conduct.mdc` (`alwaysApply: true`) — the load-bearing one:
- Inspect actual repo state before proposing changes; don't assume unseen code. Start from the
  modified files and any file the user names.
- Build on existing patterns; prefer a cleaner approach only when it stays inside the request.
- **Minimal fix**: implement only what was asked — no extra features, helpers, or drive-by
  refactors.
- Short, direct replies. If the user's intuition is wrong, say so and ask — don't assume and
  start writing.

`patterns/client-wrapper-pattern.mdc` (globs `backend/**/*_client.py`) — new or edited
`*_client.py` SDK wrappers must mirror `backend/vector_database_tests/utils/qdrant_client.py`:
one primary wrapper class; SDK client and heavy deps constructed in `__init__` and stored on
`self.__private` / `self._protected`; snake_case type-hinted public methods; external I/O wrapped
in `try / except Exception as e` logging `LOGGER.error(f"<short context>\n\t{str(e)}")`. Retrieval
methods log and return a safe sentinel (`[]`, `False`) on failure; **mutations log then `raise`**
so a failed write never looks like a success.

`cursor-rules.mdc` and `self-improvement.mdc` are meta-rules about where rule files live and when
to add new ones — relevant only when editing the rules themselves.
