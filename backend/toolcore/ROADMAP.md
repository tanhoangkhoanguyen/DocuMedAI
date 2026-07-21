# DocuMedAI — MCP Enhancement Roadmap

> Turn DocuMedAI's tool layer into a genuinely spec-compliant **Model Context Protocol** surface — without breaking the internal agent's fast path — and back it with a self-hosted MCP observability harness.

---

## 1. Context

### Why this change
DocuMedAI ships a file called [`backend/services/chatbot/mcp.py`](../services/chatbot/mcp.py) named `MCPServer`, but it is **not** Model Context Protocol. It is an in-process `Dict[str, McpToolDefinition]` of Python callables ([`mcp.py:97`](../services/chatbot/mcp.py#L97)), selected by an LLM from a **plaintext** catalog string (`format_registry`, [`mcp.py:104-111`](../services/chatbot/mcp.py#L104-L111)) and executed in a threadpool ([`nodes.py:267-277`](../services/chatbot/nodes.py#L267-L277)). There is:

- no JSON-RPC 2.0, no transport (stdio/HTTP), no client/server separation,
- no JSON-Schema tool typing (every tool takes the same untyped `ToolParameter` envelope, [`schemas.py:59-67`](../services/chatbot/constants/schemas.py#L59-L67)),
- no interoperability — no external MCP host can reach these tools.

For an **AI Infra** portfolio this naming is a liability: it implies a capability the code doesn't have.

### Intended outcome
A shared **Tool Core** consumed two ways — the internal agent calls it **directly in-process** (unchanged, no protocol overhead), and a **real MCP server** wraps the *same core* for external clients over **streamable-HTTP** with per-request JWT.

```
                    Tool Core   (shared execution core)
                   /          \    search_medical / search_user_documents / identity
                  /            \
     Internal Agent            MCP Server
  (direct in-process,           |   streamable-HTTP
   unchanged fast path)         |   JSON-RPC 2.0, JWT per request
                                ▼
                       External MCP Clients
                    (MCP Inspector, any HTTP MCP host)
```

### Constraints (non-negotiable)
- The internal agent/runtime **must NOT call MCP over the network** — it keeps direct in-process tool execution.
- MCP exists **only** as an external protocol interface over the *same shared execution core*.
- **JWT enforced for external MCP clients**; internal execution keeps using the existing authenticated app context.
- Observability must **not duplicate** the [`vector_database_tests/`](../vector_database_tests/) ann-benchmarks method. That lab measures vector-engine recall/latency; this harness measures MCP **serving/protocol** overhead — a different axis.

### Out of scope (covered by other projects)
Guardrails, rate limiting, circuit breakers, dynamic/hot-loadable registries, governance. Optimize purely for **capability serving, MCP interoperability, protocol design, resource serving, and observability**. Résumé audience: **AI Infra / Platform**.

---

## 2. Architectural spine

The whole roadmap hinges on one refactor: **extract a Tool Core** that both consumers share.

**Today's coupling** — `mcp.py` *is* the registry, the prompt formatter, AND the executor, and imports tool implementations directly (`get_medical_supporter`, `get_user_document_supporter`). Config (model/embedding/rerank) is bound at singleton construction (`get_mcp_client`, [`mcp.py:134-159`](../services/chatbot/mcp.py#L134-L159)); only `message` + `user_id` are per-call (`execute_tool_call`, [`mcp.py:116-132`](../services/chatbot/mcp.py#L116-L132)).

**Target** — a transport-agnostic **Tool Core** exposing typed `list_tools()` / `call_tool(name, args, principal)` with **JSON-Schema input contracts**. Two thin adapters sit on top:

1. **Internal adapter** — what [`nodes.py`](../services/chatbot/nodes.py) calls today, behavior preserved (LLM picks from catalog → threadpool → text results). Passes the already-authenticated `user_id` from `GraphState.user_info` as the principal ([`nodes.py:331`](../services/chatbot/nodes.py#L331)).
2. **MCP adapter** — an MCP server mapping each Tool Core tool to an MCP tool, verifying a JWT per request, deriving the principal from verified claims, serving streamable-HTTP.

Both adapters call the **same** `ToolCore.call_tool`. That single fact is the entire story: *one execution core, two serving surfaces, measured overhead between them.*

---

## Phase 1 — Extract the shared Tool Core (protocol-shaped, still in-process)

**Phase goal:** decouple tool *definitions + execution* from registry/transport, and give every tool a real JSON-Schema input contract. No behavior change for the internal agent.

### Issue 1.1 — Define typed tool contracts (JSON Schema)
- **Problem:** every tool takes the identical untyped `ToolParameter` envelope ([`schemas.py:59-67`](../services/chatbot/constants/schemas.py#L59-L67)); no per-tool schema ⇒ no MCP `inputSchema`, no validation, no introspection.
- **What to do:**
  - Add a `ToolSpec` model: `name`, `title`, `description`, `input_schema` (JSON Schema dict), `handler`, `requires_principal: bool`. Replace the frozen `McpToolDefinition` ([`schemas.py:52-56`](../services/chatbot/constants/schemas.py#L52-L56)).
  - Author arg schemas for the 3 tools: `search_medical_knowledge({query: str})`, `search_user_documents({query: str})` (principal-scoped), `identity({})`.
  - Split **runtime config** (model/embedding/rerank — infra concern; stays bound at Core construction) from **call args** (query — caller concern; validated per-call).
- **Criteria:** each tool has a valid JSON Schema; invalid args raise a typed `ToolInputError`; `identity` accepts empty args.
- **Tech:** Pydantic v2 `model_json_schema()` (schema derived from arg models = single source of truth), `jsonschema` validation at the boundary.

### Issue 1.2 — Build `ToolCore` (transport-agnostic executor)
- **Problem:** execution logic is welded to `MCPServer` and its LLM-catalog formatting; MCP can't reuse it cleanly.
- **What to do:**
  - New `backend/services/chatbot/tool_core/core.py`: `ToolCore.list_tools() -> list[ToolSpec]`, `ToolCore.call_tool(name, args: dict, principal: Principal | None) -> ToolResult`.
  - Move the 3 handlers here; they call the **unchanged** `get_medical_supporter` ([`medical_supporter.py:71`](../services/chatbot/tools/medical_supporter.py#L71)) / `get_user_document_supporter` ([`user_document_supporter.py:84`](../services/chatbot/tools/user_document_supporter.py#L84)). Preserve the `user_id`-empty short-circuit ([`user_document_supporter.py:45-47`](../services/chatbot/tools/user_document_supporter.py#L45-L47)) by enforcing it in Core as `requires_principal` so *both* surfaces get isolation for free.
  - `ToolResult` carries structured content (text block(s) today; room for richer MCP content types) + timing metadata for Phase 4.
- **Criteria:** `call_tool("search_user_documents", {...}, principal=None)` refuses (no leak); with a principal it returns the same string the old path did; byte-identical to pre-refactor for the same inputs.
- **Tech:** existing supporter singletons + Qdrant/RAG clients untouched; Core is a thin typed façade. `Principal = {user_id, source: "internal" | "mcp"}`.

### Issue 1.3 — Rewire the internal agent onto the Core (behavior-preserving)
- **Problem:** prove the fast path is unchanged and the Core is the single execution point.
- **What to do:**
  - `mcp.py` becomes a **thin internal adapter** over `ToolCore`: `format_registry()` renders the catalog from `ToolCore.list_tools()`; `has_tool`/`execute_tool_call` delegate to Core. Keep `get_mcp_client` signature so [`nodes.py:233-240`](../services/chatbot/nodes.py#L233-L240) is untouched (or a one-line import swap).
  - The planner still emits `{tool, message}` (`AGENT_PLANNER_PROMPT`, [`prompts.py:92-111`](../services/chatbot/constants/prompts.py#L92-L111)); the adapter maps `message` → the tool's `{query}` arg before calling Core.
  - Internal calls pass `Principal(user_id=state.user_info.user_id, source="internal")` — **no network, no JWT re-check**.
- **Criteria:** existing chat flow returns identical answers; `run_chatbot.py` harness still works; no new latency on the internal path (asserted in Phase 4).
- **Tech:** adapter pattern; zero change to CrewAI (it consumes pre-fetched `evidence` text, [`nodes.py:362-397`](../services/chatbot/nodes.py#L362-L397)).

---

## Phase 2 — Stand up the real MCP Server (external protocol surface)

**Phase goal:** expose the Tool Core over genuine MCP — JSON-RPC 2.0, `initialize` / `tools/list` / `tools/call`, over **streamable-HTTP** — as a **separate process/service** never in the internal agent's path.

> **Transport note:** a stdio transport was built in Issue 2.1 and later **removed** — DocuMedAI's users are served by the in-process core, and the only external surface worth maintaining for a multi-user system is HTTP. stdio references below are historical.

### Issue 2.1 — FastMCP server wrapping the Tool Core
- **Problem:** there is no protocol server at all today.
- **What to do:**
  - New `backend/mcp/server.py`: instantiate a FastMCP server; for each `ToolCore.list_tools()` spec, register an MCP tool whose `inputSchema` is the Phase-1 JSON Schema and whose handler calls `ToolCore.call_tool`.
  - Map `ToolResult` → MCP content blocks (`TextContent`); surface `ToolInputError` as a proper JSON-RPC error, not a 200 with an error string.
  - Entry points: `python -m mcp.server --transport stdio` and `--transport http` (streamable-HTTP).
- **Criteria:** `initialize` returns server capabilities; `tools/list` returns 3 tools with valid schemas; `tools/call` on `identity` works; verified end-to-end in **MCP Inspector** and a scripted stdio client.
- **Tech:** official **Python MCP SDK / FastMCP**; `mcp.server` stdio + streamable-HTTP transports. New `backend/mcp/requirements.txt` pinning `mcp`.

### Issue 2.2 — JWT enforcement for external MCP clients
- **Problem:** external clients are untrusted; internal callers are already authenticated. The two must not share a trust assumption.
- **What to do:**
  - Reuse the **existing** verifier `decode_bearer_any` ([`auth_deps.py:72-116`](../services/app/auth_deps.py#L72-L116)) — do not fork JWT logic. It already handles local HS256 (`type=local`, `id`) and Supabase tokens (`stable_supabase_user_id`).
  - streamable-HTTP: read `Authorization: Bearer`, verify, build `Principal(user_id=claims["id"], source="mcp")`, attach to the MCP session; reject `tools/call` on principal-requiring tools (`search_user_documents`) without valid claims → JSON-RPC error / 401.
  - stdio: accept the token via env var / init option (stdio has no HTTP headers), verified through the same function.
  - **Enforcement lives in the MCP adapter only** — internal `source="internal"` calls bypass it entirely.
- **Criteria:** unauthenticated external `search_user_documents` is refused; a valid user's token returns only that user's chunks; a second user's token cannot read the first's docs; `identity` works with no token.
- **Tech:** shared `auth_deps.decode_bearer_any`; per-session principal binding; the three existing isolation layers (Core `requires_principal`, Qdrant `user_id` filter [`qdrant_client.py:205`](../vector_database_tests/utils/qdrant_client.py#L205), empty-user short-circuit) all still fire.

### Issue 2.3 — Package the MCP server as a compose service
- **Problem:** must run and be demoable without polluting the app runtime.
- **What to do:**
  - Added `la-mcp-server` to [`docker-compose.yml`](../../docker-compose.yml): own container on `documedai-net`, streamable-HTTP port 8090 exposed, `mem_limit` set. The app (`la-documedai`) does **not** depend on it. Unlike `la-llm-proxy` (a standalone Go binary), the MCP server *is* the app's Python code, so it reuses the `la-documedai` image (shared `documedai` tag, built once) and overrides `command:` — no second Dockerfile, no duplicate model precache. Healthcheck is a TCP liveness probe (a bare GET to `/mcp` has no spec-defined status under streamable-HTTP).
  - Documented compose launch + an MCP-host HTTP connection snippet (Inspector + `claude_desktop_config.json`, `/mcp` URL with `Authorization: Bearer`) in `backend/mcp_server/README.md`.
- **Criteria:** `docker compose up -d la-mcp-server` serves MCP over HTTP; Inspector / an HTTP MCP client lists + calls tools; the app stack runs identically whether or not this service is up.
- **Tech:** Docker, compose service, streamable-HTTP.

---

## Phase 3 — CI contract & conformance tests

**Phase goal:** prove the system behaves per spec and that **internal and MCP paths return equivalent results**, wired into the existing [`ci_tests/`](../../ci_tests/) + [`python-ci.yml`](../../.github/workflows/python-ci.yml) machinery.

### Issue 3.1 — Tool Core unit/contract tests
- **Problem:** the current registry has **zero** dedicated tests.
- **What to do:** new `ci_tests/integration/tool_core/`: schema validity per tool; `ToolInputError` on bad args; `requires_principal` refusal path; principal isolation (user A vs user B against Qdrant, mirroring `test_qdrant_user_isolation` in [`test_qdrant_client.py`](../../ci_tests/integration/vector_db/test_qdrant_client.py)).
- **Criteria:** invalid args never reach a supporter; no-principal `search_user_documents` returns empty/refused; cross-user retrieval leaks nothing.
- **Tech:** pytest `integration` marker ([`pytest.ini`](../../pytest.ini)); reuse `fixtures/vector_db.py` seeding + `auth_headers`/`supabase_jwt` fixtures ([`conftest.py`](../../ci_tests/conftest.py)).

### Issue 3.2 — MCP protocol conformance tests
- **Problem:** must guarantee real JSON-RPC / lifecycle correctness, not just "it ran once."
- **What to do:** extend the live-HTTP harness in `ci_tests/integration/mcp/test_mcp_server.py` (loopback uvicorn, MCP SDK client) in place — not a parallel harness — and register an `mcp` pytest marker in `pytest.ini`. Assert: `initialize` advertises `serverInfo` + a non-null `tools` capability; `tools/list` matches the Core's `list_tools()` verbatim (names + schemas); `identity` happy path; a valid bearer accepted, a garbage bearer → HTTP 401.
- **Error taxonomy (input vs auth vs protocol):** the original "distinct *codes*" framing does not hold for tool calls under the MCP SDK, and by spec should not — the SDK returns tool-level failures (unknown tool, bad args, missing principal) as `CallToolResult.isError` *results*, not JSON-RPC errors, so the numeric code the adapter raises never reaches the client. Pin tool errors by their distinct **messages** (names the tool / names the validation failure / "principal"), and assert real numeric JSON-RPC codes only for genuine *protocol* errors, one test per code: `PARSE_ERROR` (-32700, unparseable bytes) and `INVALID_REQUEST` (-32600, a valid method call with no session). `METHOD_NOT_FOUND` (-32601) is **not client-reachable** (a session-less POST is rejected as -32600 before method routing; the SDK client only sends known methods over an established session) — `skip` its test with that reason rather than faking it. There is no "upstream" category to test at the protocol boundary (it would need live LLM/RAG). This is a test-only issue — no production code change; the tests pin behavior that already exists.
- **Criteria:** all lifecycle methods pass; protocol error codes spec-correct; tool-error and auth-rejection channels covered.
- **Tech:** MCP SDK **client** against a loopback HTTP server; `mcp` pytest marker in `pytest.ini` (previously only `integration` / `asyncio` were registered).

### Issue 3.3 — Cross-surface equivalence test (the money test)
- **Problem:** the core claim is "same execution core, two surfaces." Prove it.
- **What to do:** for a fixed seeded corpus + query, assert `internal ToolCore.call_tool(...)` and `MCP tools/call(...)` return equivalent content (modulo transport envelope). This backs the résumé bullet.
- **Criteria:** internal and MCP results equivalent for medical + user-doc tools with matched principals.
- **Tech:** pytest; shared fixtures; graph stays mocked (`conftest.py` `_build_graph` patch) so no LLM keys needed — Core tools hit live Qdrant only.

### Issue 3.4 — CI wiring
- **What to do:** extend [`.github/workflows/python-ci.yml`](../../.github/workflows/python-ci.yml) to (a) run the new test dirs inside `la-documedai`, (b) execute the HTTP conformance tests (they self-host a loopback uvicorn in-process; no separate service needed). Keep the mocked-graph, no-LLM-key CI contract.
- **Criteria:** CI green on PRs to `main`/`develop`; MCP conformance + equivalence gate every merge.
- **Tech:** GitHub Actions, `docker compose -f docker-compose.yml -f docker-compose.ci.yml`.

---

## Phase 4 — Self-hosted MCP observability harness (headline feat)

**Phase goal:** measure MCP *serving* characteristics — a **different axis** from the `vector_database_tests/` recall/latency lab. That lab benchmarks vector engines; this measures **protocol/serving overhead and reliability of the MCP surface**. Do not reuse its ann-benchmarks harness.

### Issue 4.1 — Per-call instrumentation in the Tool Core
- **Problem:** no app-level latency measurement exists anywhere (only the Go proxy has metrics, and nothing scrapes it).
- **What to do:** wrap `ToolCore.call_tool` to record `{tool, surface: internal|mcp, phase, duration, outcome}`. Emit a structured log line **and** Prometheus metrics. Tag **surface** so internal-vs-MCP is comparable from one metric.
- **Criteria:** every call produces a timed record tagged by tool + surface + outcome.
- **Tech:** `time.perf_counter`, `prometheus_client` (histograms with explicit buckets), JSON logs (consistent with the proxy's Loki-friendly logging).

### Issue 4.2 — MCP protocol metrics + overhead comparison
- **Problem:** the standout, non-obvious metric — *what does the protocol cost vs calling the core directly?*
- **What to do:**
  - Per-tool **P50 / P95 / P99** latency (histograms).
  - `mcp_requests_total{method,outcome}` for `initialize` / `tools/list` / `tools/call` → success rates.
  - **Protocol-overhead metric:** same tool+args via (a) direct `ToolCore.call_tool` and (b) MCP `tools/call`; report the delta (absolute ms + %). The differentiator.
  - **Error taxonomy** counter: `input_error` / `auth_error` / `upstream_error` / `not_found`.
- **Criteria:** a `/metrics` endpoint on the MCP server exposes all of the above; a small `benchmark_mcp.py` driver produces a reproducible overhead report (JSON — its **own** metrics, not the recall harness).
- **Tech:** `prometheus_client` on the MCP server; a fixed-workload async driver (open-loop send-time latency, echoing the proxy/lab discipline against coordinated omission, without importing the lab's recall code).

### Issue 4.3 — Dashboard + repro runbook
- **Problem:** metrics are exposed but nothing visualizes them (no Prometheus/Grafana config in the repo today).
- **What to do:** add a minimal `prometheus.yml` scraping `la-mcp-server` + `la-llm-proxy`, and a Grafana dashboard JSON (per-tool P50/P95/P99, success rates, internal-vs-MCP overhead panel, error taxonomy). Wire as an optional compose profile (mirror `--profile vectordb-lab`). Runbook in `backend/mcp/README.md`.
- **Criteria:** `docker compose --profile observability up` shows live panels; overhead panel reads real numbers; one command regenerates the JSON report.
- **Tech:** Prometheus, Grafana (provisioned dashboard JSON), compose profile.

---

## 3. Statistics to capture (feed the aura bullets)

Collected in Phase 3 (correctness) + Phase 4 (performance). Fill `<>` from real runs.

| Metric | Source | Bullet use |
|---|---|---|
| MCP protocol overhead: `<X>` ms / `<Y>`% vs direct in-process | Issue 4.2 | **the** differentiator — quantified design tradeoff |
| Per-tool P50 / P95 / P99 (`tools/call`) | Issue 4.2 | serving-latency credibility |
| `initialize` / `tools/list` / `tools/call` success rate `<>`% | Issue 4.2 | protocol reliability |
| # CI contract + conformance tests gating merge (`<N>`) | Phase 3 | rigor signal |
| Cross-surface equivalence: internal ≡ MCP results | Issue 3.3 | proves single-core architecture |
| Tools over spec-compliant MCP (`3`) over `streamable-HTTP` | Phase 2 | interoperability |
| Error taxonomy coverage (input/auth/upstream/not-found) | Issue 4.2 | observability maturity |

### Draft aura bullet points (AI Infra flavored — finalize with real numbers)
- **"Designed a shared Tool Core serving one execution path to both an internal LangGraph agent (direct, in-process) and a spec-compliant MCP server (JSON-RPC 2.0 over streamable-HTTP), exposing 3 medical-RAG tools to any external MCP host with per-request JWT enforcement — zero added latency on the internal path."**
- **"Built a self-hosted MCP observability harness measuring per-tool P50/P95/P99 and protocol overhead vs direct execution (`<Y>`%) — quantifying the exact cost of interoperability instead of guessing."**
- **"Gated every merge with `<N>` MCP conformance + cross-surface equivalence tests (lifecycle handshake, JSON-RPC error taxonomy, multi-tenant isolation across the protocol boundary)."**
- **"Enforced multi-tenant document isolation across the MCP boundary by reusing the app's JWT verifier as a per-session principal — proven leak-free by adversarial cross-user CI tests."**

---

## 4. Critical files

| Action | Path | Note |
|---|---|---|
| **New** | `backend/services/chatbot/tool_core/core.py` | Tool Core: `list_tools` / `call_tool` / `ToolSpec` / `Principal` / `ToolResult` |
| Modify | [`backend/services/chatbot/constants/schemas.py:52-67`](../services/chatbot/constants/schemas.py#L52-L67) | replace `McpToolDefinition`/`ToolParameter` with typed `ToolSpec` + per-tool arg models |
| Modify | [`backend/services/chatbot/mcp.py`](../services/chatbot/mcp.py) | becomes thin **internal adapter** over Tool Core; keep `get_mcp_client` signature |
| Reuse (unchanged) | [`medical_supporter.py:71`](../services/chatbot/tools/medical_supporter.py#L71), [`user_document_supporter.py:84`](../services/chatbot/tools/user_document_supporter.py#L84), [`rag.py`](../services/chatbot/tools/rag.py), [`qdrant_client.py`](../vector_database_tests/utils/qdrant_client.py) | Core wraps these; do not touch execution/isolation |
| Touch (import only) | [`backend/services/chatbot/nodes.py:233-277`](../services/chatbot/nodes.py#L233-L277) | still calls the adapter; behavior identical |
| **New** | `backend/mcp/server.py`, `backend/mcp/requirements.txt`, `backend/mcp/README.md` | FastMCP server, transports, runbook |
| Reuse | [`backend/services/app/auth_deps.py:72-116`](../services/app/auth_deps.py#L72-L116) | `decode_bearer_any` for external MCP JWT — do not fork |
| **New** | `ci_tests/integration/tool_core/`, `ci_tests/integration/mcp/` | contract + conformance + equivalence tests |
| Modify | [`pytest.ini`](../../pytest.ini), [`.github/workflows/python-ci.yml`](../../.github/workflows/python-ci.yml) | add `mcp` marker; wire new suites + `la-mcp-server` |
| **New** | [`docker-compose.yml`](../../docker-compose.yml) (`la-mcp-server`), `backend/mcp/prometheus.yml`, Grafana dashboard JSON | serving + observability, optional profiles |

---

## 5. Verification (end-to-end)

1. **Internal unchanged:** run `python backend/services/chatbot/run_chatbot.py` + a live chat via FastAPI — answers identical to pre-refactor; assert no internal-path latency regression (Phase 4 metric, `source=internal`).
2. **MCP live:** `docker compose up -d la-mcp-server`; connect **MCP Inspector** + **Claude Desktop** → `initialize`, `tools/list` (3 tools w/ schemas), `tools/call identity`.
3. **Auth boundary:** external `tools/call search_user_documents` without token → refused; with user-A token → only A's chunks; with user-B token → cannot read A's (isolation across protocol).
4. **CI:** `docker compose -f docker-compose.yml -f docker-compose.ci.yml exec -T la-documedai pytest -q ci_tests/integration/tool_core ci_tests/integration/mcp` — contract + conformance + equivalence green.
5. **Observability:** `docker compose --profile observability up`; run `benchmark_mcp.py`; confirm P50/P95/P99, success rates, and the **internal-vs-MCP overhead** panel show real numbers; JSON report regenerates on demand.
