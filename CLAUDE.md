# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DocuMedAI is a full-stack AI-powered medical document analysis system. Users upload medical documents, then ask questions via a chat interface. The backend uses a multi-agent LangGraph workflow with RAG retrieval across vector databases. The system supports persistent conversation memory, Redis caching, and JWT + Supabase authentication.

**Key concept**: The LangGraph `StateGraph` routes each message through TopicChecker → MessageAnalysis → LongTermMemoryRetriever → Agents (CrewAI) → SchemaUpdater. RAG uses Qdrant retrieval with cross-encoder reranking. When a topic change is detected, the prior conversation is archived to a Qdrant long-term memory collection.

## Architecture

| Layer | Tech | Location |
|-------|------|----------|
| Frontend | Next.js 15 / React 19 / TypeScript | `frontend/` |
| Backend API | FastAPI (port 2010) | `backend/services/app/` |
| Agent workflow | LangGraph StateGraph | `backend/services/chatbot/` |
| Multi-agent | CrewAI | `nodes.py` — Agents node |
| RAG pipeline | Qdrant retrieval + BAAI reranker | `backend/services/chatbot/tools/rag.py` |
| LLM proxy | Go gateway (rate limit, retry, circuit breaker, dedup) in front of Gemini's OpenAI-compat endpoint | `backend/llm-proxy/` |
| Memory cache | Redis (TTL 1800s → flush to MongoDB) | `backend/services/utils/redis_client.py` |
| Persistence | MongoDB | `backend/services/utils/mongo_client.py` |
| Auth | JWT + Supabase SSR | `backend/services/app/auth_api.py`, `frontend/lib/supabase/` |
| Vector DB | Qdrant (default); alternatives benchmarked in `backend/vector_database_tests/` | `backend/services/chatbot/tools/` |
| MCP tools | Medical support tool registry | `backend/services/chatbot/mcp.py` |
| Encryption | Pattern cipher for stored messages | `backend/services/chatbot/tools/pattern_cipher.py` |

**Important**: `backend/services/app/` holds the FastAPI routes and workspace layer. `backend/services/chatbot/` holds the LangGraph graph, nodes, and tools. `backend/services/utils/` holds shared DB clients (MongoDB, Redis, Supabase).

**LLM proxy**: All chat-completion calls route through the Go `la-llm-proxy` service (port 8081), which forwards to Gemini's OpenAI-compatible endpoint, not to the provider directly. Embeddings and the reranker run locally and bypass it. Each `ChatOpenAI`/`crewai.LLM` is constructed with `base_url=get_llm_base_url()` (`backend/services/chatbot/llm_config.py`), driven by the `LLM_PROXY_BASE_URL` env var. Set `LLM_PROXY_BASE_URL=""` to bypass the proxy. See `backend/llm-proxy/README.md`.

## Common Commands

### Docker (primary workflow)
```bash
docker compose up -d --build          # Start all services
docker compose down                   # Stop all services
docker compose logs -f la-backend     # Tail backend logs
docker compose --profile vectordb-lab up -d  # Include optional vector DB lab services
```

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
Tests live in `ci_tests/` and run against live services inside the `la-backend` container
(see `pytest.ini`: `testpaths = ci_tests`, `pythonpath = backend .`). They are integration
tests, not unit tests — Mongo/Redis/Qdrant must be reachable.

```bash
# CI command (matches .github/workflows/python-ci.yml) — run inside the running stack
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.ci.yml"
$COMPOSE up -d --build --wait la-qdrant la-mongo la-redis
$COMPOSE up -d --build la-backend
$COMPOSE exec -T la-backend pytest -q \
  ci_tests/integration/utils \
  ci_tests/integration/api \
  ci_tests/integration/vector_db/test_qdrant_client.py

# Single file inside the container
$COMPOSE exec -T la-backend pytest -q ci_tests/integration/api/test_chat_api.py

$COMPOSE down -v
```

Markers (`pytest.ini`): `integration` (live services), `vectordb` (Milvus/Weaviate/Vespa/
ChromaDB — the `vectordb-lab` CI job; requires `requirements-dev.txt` clients).

`docker-compose.ci.yml` overrides `la-backend` to idle (`sleep infinity`, healthcheck
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
| LLM proxy (Go) | 8081 |

Swagger UI: `http://localhost:2010/docs`
LLM proxy metrics: `http://localhost:8081/metrics`

## Key Files

| File | Purpose |
|------|---------|
| `backend/services/app/run_app.py` | FastAPI entry point; graph config constants live here |
| `backend/services/app/chat_api.py` | Chat endpoints; calls `ChatGraph.ainvoke()` |
| `backend/services/app/chatbot_workspace.py` | Redis/Mongo workspace layer; manages TTL flush |
| `backend/services/app/auth_api.py` | Auth endpoints + JWT dependency injection |
| `backend/services/chatbot/workflow.py` | Builds and compiles the LangGraph StateGraph |
| `backend/services/chatbot/nodes.py` | All 5 graph node implementations |
| `backend/services/chatbot/tools/rag.py` | RAG pipeline (paraphrase → retrieve → rerank → threshold) |
| `backend/services/chatbot/constants/schemas.py` | Pydantic models: `GraphState`, `ChatMessageState`, etc. |
| `backend/services/chatbot/constants/prompts.py` | All LLM prompt templates |
| `frontend/components/ChatLayout.tsx` | Main chat UI |
| `frontend/lib/internal-api.ts` | Frontend → backend API client |

## Graph Config (run_app.py)

```python
chat_model = "gpt-4o-mini"
embedding_model = "sentence-transformers/all-MiniLM-L6-v2"  # dim 384
reranking_model = "BAAI/bge-reranker-v2-m3"
qdrant_threshold = 0.25
reranking_threshold = -5
topic_threshold = -5
shortterm_memory_size = 5
max_revision_cycles = 3
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
2. `chat_api.py` extracts JWT, loads workspace, calls `ChatGraph.ainvoke()`
3. LangGraph: `TopicChecker` → conditional edge → `MessageAnalysis` → `LongTermMemoryRetriever` → `Agents` → `SchemaUpdater`
4. `Agents` node runs CrewAI with RAG tool (Qdrant retrieval, cross-encoder reranker)
5. Response stored in Redis; flushed to MongoDB on TTL expiry or explicit save
6. Streaming response returned to frontend

## Data Flow: Document Upload

1. PDFs processed by `backend/vector_database_tests/data_processing.py`
2. Uploaded via `data_uploading.py` to Qdrant (and optionally other vector DBs)
3. Indexed with `all-MiniLM-L6-v2` embeddings (dim 384)

## Vector DB Benchmark Lab (`backend/vector_database_tests/`)

A standalone benchmark to choose which engine to self-host (Qdrant default vs Milvus,
Weaviate, Vespa, ChromaDB). It is **not** part of the running app — it's a decision tool.
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

GitHub Actions (`.github/workflows/`): `python-ci.yml` (backend integration tests +
`vectordb-lab` job) and `frontend-ci.yml` (`npm run lint` + `npm run build`). Triggered on
PRs and pushes to `main`/`develop`. CI writes a throwaway `.env` with test secrets; the
backend graph is mocked, so no LLM API keys are needed.
