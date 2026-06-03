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
| Memory cache | Redis (TTL 1800s → flush to MongoDB) | `backend/services/utils/redis_client.py` |
| Persistence | MongoDB | `backend/services/utils/mongo_client.py` |
| Auth | JWT + Supabase SSR | `backend/services/app/auth_api.py`, `frontend/lib/supabase/` |
| Vector DB | Qdrant (default); alternatives in `vector_database_tests/` | `backend/services/chatbot/tools/` |
| MCP tools | Medical support tool registry | `backend/services/chatbot/mcp.py` |
| Encryption | Pattern cipher for stored messages | `backend/services/chatbot/tools/pattern_cipher.py` |

**Important**: `backend/services/app/` holds the FastAPI routes and workspace layer. `backend/services/chatbot/` holds the LangGraph graph, nodes, and tools. `backend/services/utils/` holds shared DB clients (MongoDB, Redis, Supabase).

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
pip install -r backend/requirements-prod.txt
python backend/services/app/run_app.py        # FastAPI server — http://localhost:2010
python backend/services/chatbot/run_chatbot.py  # Graph-only test harness (no HTTP)
```

### Tests
```bash
pytest tests/                              # All tests
pytest tests/test_db_retrieval.py -v      # Single test file
```

## Service Ports

| Service | Port |
|---------|------|
| Backend API (FastAPI) | 2010 |
| Frontend (Next.js) | 2011 |
| Qdrant | 6333 |
| MongoDB | 27017 |
| Redis | 6379 |

Swagger UI: `http://localhost:2010/docs`

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

1. PDFs processed by `vector_database_tests/data_processing.py`
2. Uploaded via `data_uploading.py` to Qdrant (and optionally other vector DBs)
3. Indexed with `all-MiniLM-L6-v2` embeddings (dim 384)

## Rules

- Read `.claude/rules/core-behavior.md` before acting
