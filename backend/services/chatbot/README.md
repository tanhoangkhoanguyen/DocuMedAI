# Chatbot Service (`services/chatbot`)

The chatbot service is the reasoning core of DocuMedAI. It turns a user message
into a grounded answer by routing it through a **LangGraph `StateGraph`** whose
nodes analyze the message, recall memory, run a multi-agent (CrewAI) answering
step over a RAG pipeline, and update conversation state.

## Overview

```
message ─▶ TopicChecker ─▶ MessageAnalysis ─▶ LongTermMemoryRetriever ─▶ Agents ─▶ SchemaUpdater ─▶ reply
```

| File | Purpose |
|------|---------|
| `workflow.py` | Builds and compiles the LangGraph `StateGraph`. |
| `nodes.py` | The graph node implementations. |
| `../../utils/rag.py` | RAG pipeline: paraphrase → retrieve (Qdrant) → cross-encoder rerank → threshold. |
| `../../toolcore/tools/medical_supporter.py` | Retrieves medical terms from the `MedicalTerms` Qdrant collection. |
| `../../utils/pattern_cipher.py` | Deterministic `uuid5` ids + bcrypt helpers. Despite the name it does **not** encrypt anything. |
| `../../toolcore/core.py` | In-memory tool registry + `call_tool`, the single isolation enforcement point. |
| `constants/schemas.py` | Pydantic models (`GraphState`, `ChatMessageState`, `ToolParameter`, …). |
| `constants/prompts.py` | LLM prompt templates. |
| `seed_prod_data.py` | Startup seeder that loads `prod_dataset/` into the `MedicalTerms` collection. |
| `run_chatbot.py` | Graph-only test harness (no HTTP). |

RAG retrieval and the medical-term tool both read from the **`MedicalTerms`**
Qdrant collection. That collection is populated from `prod_dataset/` at backend
startup — see below.

## `prod_dataset/` — the seeded knowledge base

At container startup, `entrypoint.sh` waits for Qdrant and runs
`python -m services.chatbot.seed_prod_data`, which uploads every `*.jsonl` file in
`prod_dataset/` into the `MedicalTerms` collection.

The seeder is **idempotent**: if `MedicalTerms` already exists and is non-empty it
does nothing. To force a rebuild (wipe + re-upload), run it with `SEED_FORCE=1`:

```bash
docker compose exec la-documedai sh -c "SEED_FORCE=1 python -m services.chatbot.seed_prod_data"
```

Adding files: drop additional `*.jsonl` files into `prod_dataset/`. They are picked
up on the next **fresh** seed (empty collection or `SEED_FORCE=1`) — a non-empty
collection is left untouched, so simply restarting will not ingest new files.

### STRICT pre-embedded format (required)

The seeder does **not** compute embeddings. Every file in `prod_dataset/` must be
**pre-embedded offline** with the configured embedding model and stored as JSONL,
one object per line, with exactly these fields:

| Field | Type | Meaning |
|-------|------|---------|
| `id` | `str` \| `int` | Stable, unique point id. |
| `split_text` | `str` | The text chunk; stored as the Qdrant payload `{"query": ...}`. |
| `embedded_test` | `list[float]` | The precomputed embedding vector. **Must** be length **384**. |

Example line:

```json
{"id": "ceb44a0c-611c-50b9-b3e7-5b5774357acb", "title": "Paracetamol poisoning", "split_text": "Paracetamol poisoning, also known as ...", "embedded_test": [0.0123, -0.0456, ...]}
```

(Extra fields such as `title` are allowed and ignored.)

**Embedding model / dimension** (must match `run_app.py` graph config):

```
embedding_model     = sentence-transformers/all-MiniLM-L6-v2
embedding_dimension = 384   (cosine distance)
```

Records missing a required field, or whose `embedded_test` is not a length-384
list, are **rejected** — the seeder raises and the container fails to start
(`set -euo pipefail`), by design, so malformed data can never be served silently.
Pre-embed your documents with `all-MiniLM-L6-v2` before dropping them in.
