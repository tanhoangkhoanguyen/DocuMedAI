# DocuMedAI MCP Server

A real **Model Context Protocol** server (JSON-RPC 2.0) exposing DocuMedAI's three RAG
tools to any external MCP host. It wraps the **same** `toolcore` execution core the
internal agent uses — one core, two surfaces.

> Named `mcp_server` (not `mcp`) so it never shadows the official `mcp` SDK package.
> Nothing to install: `mcp` already ships in the `la-backend` image (via crewai).

## Tools

| Tool | Auth | Input |
|------|------|-------|
| `identity` | no | none |
| `search_medical_knowledge` | no | `{query: str}` |
| `search_user_documents` | **yes** | `{query: str}` — refused without a principal |

## Auth

The MCP boundary is the **only** auth surface — internal `source="internal"` callers
bypass it. Tokens are verified by the shared `services.app.auth_deps.decode_bearer_any`
(local HS256 + Supabase), turned into a `Principal(source="mcp")` in
[`auth.py`](auth.py), and threaded into every `tools/call`.

| Token | Result |
|-------|--------|
| absent | anonymous — `identity` / `search_medical_knowledge` work; `search_user_documents` refused |
| valid | `Principal(user_id=…, source="mcp")` — sees only that user's chunks |
| present but invalid | **rejected** — HTTP `401`; stdio aborts at startup |

- **HTTP:** send `Authorization: Bearer <jwt>`. An ASGI step verifies it per request and
  binds the principal for that request's scope before the session manager runs.
- **stdio:** no HTTP headers, so pass the token via the `MCP_AUTH_TOKEN` env var. It is
  read once at process start (one process = one client = one principal).

## Prerequisites

With Docker Desktop running, bring up the **CI** stack from the repo root. Both compose
files are required: the `.ci.yml` override runs `la-backend` idle at `/workspace` with
`PYTHONPATH` set, so tests run via `exec` (the prod backend can't host tests).

```powershell
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --build --wait la-qdrant la-mongo la-redis
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --build la-backend
```

Everything below runs **inside** `la-backend`; the CI container already puts the code
on `PYTHONPATH`, so no path flags are needed.

## Verify

```powershell
docker compose -f docker-compose.yml -f docker-compose.ci.yml exec -T la-backend pytest -q ci_tests/integration/mcp/test_mcp_server.py
```

Expected: `3 passed` — tools/list (3), tools/call identity, principal refusal. This
spawns the server over stdio and drives the real JSON-RPC lifecycle end to end.

### Interactive Testing (MCP Inspector)

Inspector *is* the client — it runs `initialize → tools/list → tools/call` for you and
shows the JSON-RPC in a browser. Needs Node/npx on your host:

```powershell
npx @modelcontextprotocol/inspector docker compose -f docker-compose.yml -f docker-compose.ci.yml exec -T la-backend python -m mcp_server --transport stdio
```

In the UI: open **Tools** (expect 3 with schemas) and **call** `identity`. With no
`MCP_AUTH_TOKEN` set, `search_user_documents` returns a protocol error (principal
required); set `MCP_AUTH_TOKEN=<jwt>` to have it return that user's chunks.

### Manual Server Launch

```powershell
# stdio
docker compose -f docker-compose.yml -f docker-compose.ci.yml exec la-backend python -m mcp_server --transport stdio

# streamable-HTTP (port 8090, path /mcp) — needs port 8090 published from
# la-backend; a permanent service is Issue 2.3's job
docker compose -f docker-compose.yml -f docker-compose.ci.yml exec la-backend python -m mcp_server --transport http --host 0.0.0.0 --port 8090
```
