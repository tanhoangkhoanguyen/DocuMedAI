# DocuMedAI

AI-powered medical document chat with a LangGraph multi-agent backend, Qdrant RAG, and Next.js frontend.

## Quick start (Docker)

```bash
cp .env.example .env   # fill in API keys and secrets
docker compose up -d --build
```

| Service | URL |
|---------|-----|
| Frontend | http://localhost:2011 |
| Backend API | http://localhost:2010/docs |

## Optional vector DB benchmarks

```bash
docker compose --profile vectordb-lab up -d
pip install -r backend/requirements-dev.txt
```

See [CLAUDE.md](CLAUDE.md) for architecture and graph configuration.