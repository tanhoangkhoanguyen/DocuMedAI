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
| RAG pipeline | Qdrant retrieval + BAAI reranker | `backend/services/chatbot/tools/rag.py` |
| LLMGuard | Go gateway (rate limit, retry, circuit breaker, dedup) in front of Gemini's OpenAI-compat endpoint | `backend/llmguard/` |
| Memory cache | Redis (TTL 1800s → flush to MongoDB) | `backend/services/utils/redis_client.py` |
| Persistence | MongoDB | `backend/services/utils/mongo_client.py` |
| Auth | JWT + Supabase SSR | `backend/services/app/auth_api.py`, `frontend/lib/supabase/` |
| Vector DB | Qdrant (default); alternatives benchmarked in `backend/vector_database_tests/` | `backend/toolcore/tools/` |
| Tool Core | In-memory tool registry + the single enforcement point (`MCPServer.call_tool`) | `backend/toolcore/core.py` |
| MCP server | Real MCP protocol (JSON-RPC 2.0 over streamable-HTTP, official `mcp` SDK) at `/mcp` on port 8090 | `backend/mcp_server/` |
| ID hashing | `pattern_cipher.py` does NOT encrypt — it's `uuid5` deterministic IDs + bcrypt helpers (the bcrypt helpers are currently unused; auth stores plaintext passwords). No message encryption exists anywhere. | `backend/services/utils/pattern_cipher.py` |

**Important**: `backend/services/app/` holds the FastAPI routes and workspace layer. `backend/services/chatbot/` holds the LangGraph graph, nodes, and tools. `backend/services/utils/` holds shared DB clients (MongoDB, Redis, Supabase).

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

**LLMGuard**: All chat-completion calls route through the Go `la-llmguard` service (port 8081), which forwards to Gemini's OpenAI-compatible endpoint, not to the provider directly. Embeddings and the reranker run locally and bypass it. Each `ChatOpenAI`/`crewai.LLM` is constructed with `base_url=get_llm_base_url()` (`backend/services/chatbot/llm_config.py`), driven by the `LLM_PROXY_BASE_URL` env var. Set `LLM_PROXY_BASE_URL=""` to bypass it. See `backend/llmguard/README.md`.

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

Swagger UI: `http://localhost:2010/docs`
LLMGuard metrics: `http://localhost:8081/metrics`
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
| `backend/services/chatbot/tools/rag.py` | RAG pipeline (paraphrase → retrieve → rerank → threshold) |
| `backend/services/chatbot/constants/schemas.py` | Pydantic models: `GraphState`, `ChatMessageState`, etc. |
| `backend/services/chatbot/constants/prompts.py` | All LLM prompt templates |
| `frontend/components/ChatLayout.tsx` | Main chat UI; hand-rolled SSE parser for the streaming endpoint |
| `frontend/lib/proxy-to-backend.ts` | Frontend → backend API client (buffered HTTP; `internal-api.ts` under `frontend/lib/utils/` is just an env-var reader) |
| `backend/toolcore/core.py` | Tool registry + `call_tool` — the single isolation enforcement point for both surfaces |
| `backend/mcp_server/__main__.py` | MCP ASGI app: per-request bearer auth → `StreamableHTTPSessionManager`, `/mcp` + `/metrics` |
| `backend/mcp_server/README.md` | MCP runbook + measured overhead/capacity benchmarks |
| `docs/mcp-loadtest-runbook.md` | Two-VM GCP setup for the serving-capacity load test |

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

Create `.env` in project root:
```
OPENAI_API_KEY=
GEMINI_API_KEY=
LANGCHAIN_API_KEY=
LANGCHAIN_TRACING_V2=true
SUPABASE_JWT_SECRET=
AUTH_JWT_SECRET=
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
```

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

GitHub Actions (`.github/workflows/`) — exactly two workflows: `python-ci.yml` and
`frontend-ci.yml` (`npm run lint` + `npm run build`). `python-ci.yml` has a single `backend`
job that runs three independent `pytest` steps (App / Vector DB / MCP), each `if: always()`
so one failure doesn't mask the others. Triggered on PRs and pushes to
`main`/`develop`/`feature/Setup-CI`. CI writes a throwaway `.env` with test secrets and sets
`SKIP_MODEL_PRECACHE=1`; the backend graph is mocked, so no LLM API keys are needed.
