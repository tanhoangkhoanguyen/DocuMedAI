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

## Observability

Live server-side metrics — two families, both on the default Prometheus registry and served
at `http://localhost:8090/metrics`:

| Metric | Labels | Source |
|--------|--------|--------|
| `toolcore_tool_calls_total`, `toolcore_tool_call_duration_seconds` | `tool`, `surface` (internal\|mcp), `outcome` | every `call_tool` (both surfaces) — [`toolcore/observability.py`](../toolcore/observability.py) |
| `mcp_requests_total` | `method`, `outcome` | per JSON-RPC request — [`metrics.py`](metrics.py) |

`outcome` ∈ `success` \| `not_found` \| `input_error` \| `auth_error` \| `upstream_error`.
Because both surfaces record on the same histogram tagged by `surface`, internal-vs-MCP
overhead is one PromQL query.

### Dashboard (Prometheus + Grafana)

Optional compose profile. Needs `la-mcp-server` up:

```powershell
docker compose up -d --build la-mcp-server                 # exposes /metrics
docker compose --profile observability up -d la-prometheus la-grafana
```

- Prometheus: `http://localhost:9090` (scrapes `la-mcp-server` + `la-llm-proxy`).
- Grafana: `http://localhost:3000` (anonymous admin) → **DocuMedAI — MCP Observability**:
  per-tool P50/P95/P99, MCP success rate by method, internal-vs-MCP overhead, error taxonomy.

Panels stay flat until traffic flows — run either benchmark below (or drive the server) to
populate them.

## Benchmarks

Two benchmarks measure two different things. Both write a JSON report (`--out`) and echo the
coordinated-omission discipline of `vector_database_tests/` (latency from each request's
*ideal* send time). Both default to `identity`, so no uploaded documents or LLM keys are
needed — the numbers are the **protocol/serving** cost, not RAG cost.

### 1. Protocol overhead — `overhead_benchmark.py` (local)

*What does one MCP call cost vs calling the core directly?* Times the same tool+args
in-process (`call_tool`) and over streamable-HTTP (`ClientSession.call_tool`), single call
in flight, and reports the delta at each percentile. Run on **loopback** so network RTT ≈ 0
and the number isolates pure serialization + JSON-RPC + framing cost.

```powershell
# from inside la-mcp-server (or any env with the code + AUTH_JWT_SECRET on PYTHONPATH)
docker compose exec la-mcp-server python -m mcp_server.overhead_benchmark --n 10000 --qps 50 --out overhead.json
```

Output: per-tool `{direct, mcp}` P50/P95/P99 plus `overhead: {p50,p95,p99}` with
`overhead_ms` / `overhead_pct` — the "cost of the protocol" number.

### 2. Serving capacity — `loadtest_mcp.py` (remote VM)

*How much concurrent load can the server sustain, and how does tail latency degrade as
offered load rises?* An open-loop QPS ladder over N persistent sessions; per rung it reports
`achieved_rps` vs target (the saturation signal), P50/P95/P99 under load, success rate, and
an outcome breakdown (`success` / `tool_error` / `transport_error`).

Because this measures the **network + serving** path, run it client→server across machines,
not on loopback. On GCP:

```bash
# 1. Server VM: bring up the MCP server (opens :8090)
docker compose up -d --build la-mcp-server la-qdrant la-mongo la-redis
#    Allow TCP 8090 from the client VM (firewall rule / same VPC).

# 2. Client VM (separate machine, same AUTH_JWT_SECRET on PYTHONPATH):
python -m mcp_server.loadtest_mcp \
    --url http://<server-vm-ip>:8090/mcp/ \
    --qps 50 100 200 400 800 --duration 30 --warmup 5 --concurrency 64 \
    --out capacity.json
```

Sustainable throughput is the highest rung where `achieved_rps ≈ target` and P99 stays
bounded; the first rung where `achieved_rps` falls short is the saturation point.
