# DocuMedAI MCP Server

A real **Model Context Protocol** server (JSON-RPC 2.0) exposing DocuMedAI's three RAG
tools to external MCP clients over **streamable-HTTP**. It wraps the **same** `toolcore`
execution core the internal agent uses — one core, two surfaces (in-process + HTTP).

> Named `mcp_server` (not `mcp`) so it never shadows the official `mcp` SDK package.
> Nothing to install: `mcp` already ships in the `la-documedai` image (via crewai).

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
| present but invalid | **rejected** — HTTP `401` before any tool runs |

Send `Authorization: Bearer <jwt>`. An ASGI step verifies it per request and binds the
principal for that request's scope before the session manager runs.

## Prerequisites

With Docker Desktop running, bring up the **CI** stack from the repo root. Both compose
files are required: the `.ci.yml` override runs `la-documedai` idle at `/workspace` with
`PYTHONPATH` set, so tests run via `exec` (the prod backend can't host tests).

```powershell
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --build --wait la-qdrant la-mongo la-redis
docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --build la-documedai
```

Everything below runs **inside** `la-documedai`; the CI container already puts the code
on `PYTHONPATH`, so no path flags are needed.

## Verify

```powershell
docker compose -f docker-compose.yml -f docker-compose.ci.yml exec -T la-documedai pytest -q ci_tests/integration/mcp/test_mcp_server.py
```

Expected: all pass — tools/list (3), tools/call identity, no-principal refusal, and a
garbage bearer rejected with `401`. The test starts the real HTTP app on a loopback
uvicorn and drives the JSON-RPC lifecycle with the official MCP SDK client end to end.
(A *successful* `search_user_documents` needs seeded Qdrant + LLM keys, so that path is
covered by the isolation tests in Issue 3.1/3.3, not here.)

## Run as a service

`la-mcp-server` is its own compose service — it reuses the `la-documedai` image (same code
and deps) with a different command, and serves `/mcp` on port **8090**. The app does not
depend on it; the backend stack runs identically whether or not it is up.

```powershell
docker compose up -d --build la-mcp-server        # serves http://localhost:8090/mcp
docker compose logs -f la-mcp-server
```

## Connect an MCP host

Point any streamable-HTTP MCP client at `http://localhost:8090/mcp` and send
`Authorization: Bearer <jwt>` to exercise `search_user_documents`; with no header, that
tool returns a protocol error (principal required) while `identity` / `search_medical_knowledge`
still work.

**MCP Inspector** — Inspector *is* the client: it runs `initialize → tools/list → tools/call`
and shows the JSON-RPC in a browser. Set the transport to **Streamable HTTP**, URL
`http://localhost:8090/mcp`, and add the `Authorization` header.

**Claude Desktop** (`claude_desktop_config.json`) — a remote HTTP MCP server:

```json
{
  "mcpServers": {
    "documedai": {
      "type": "http",
      "url": "http://localhost:8090/mcp",
      "headers": { "Authorization": "Bearer <jwt>" }
    }
  }
}
```
