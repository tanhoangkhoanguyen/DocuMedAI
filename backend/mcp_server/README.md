# DocuMedAI MCP Server

A real **Model Context Protocol** server (JSON-RPC 2.0) exposing DocuMedAI's three RAG
tools to external MCP clients over **streamable-HTTP**. It wraps the **same** `toolcore`
execution core the internal agent uses — one core, two surfaces (in-process + HTTP).

> Named `mcp_server` (not `mcp`) so it never shadows the official `mcp` SDK package.
> Nothing to install: `mcp` already ships in the `la-documedai` image (via crewai).

**Contents:** [Run it](#run-it) · [Tools](#tools) · [Auth](#auth) ·
[Connect an MCP host](#connect-an-mcp-host) · [Tests](#tests) ·
[Observability](#observability) · [Performance](#performance)

## Run it

From the repo root. Requires `AUTH_JWT_SECRET` in `.env`:

```powershell
docker compose up -d --build la-mcp-server
docker compose logs -f la-mcp-server    # wait for: serving streamable-HTTP at http://0.0.0.0:8090/mcp
```

Confirm it answers:

```powershell
curl -s http://localhost:8090/metrics/ | head -3
```

`la-mcp-server` is its own compose service, reusing the `la-documedai` image (same code and
deps) with a different command. The app does **not** depend on it — the backend stack runs
identically whether or not it is up.

Two gotchas worth knowing up front:

> **Trailing slash matters.** `/mcp` and `/metrics` are Starlette `Mount`s, so the slashless
> form 307-redirects. `curl` does not follow redirects by default and returns an empty body
> with exit 0 — which looks exactly like a dead server. Use `/metrics/` and `/mcp/`.

> **`docker ps` can lie.** The healthcheck is a TCP liveness probe only (a bare `GET /mcp` has
> no spec-defined status under streamable-HTTP), so `healthy` means the port is open, not that
> the app responds. Trust the curl above.

## Tools

| Tool | Auth | Input |
|------|------|-------|
| `identity` | no | none |
| `search_medical_knowledge` | no | `{query: str}` |
| `search_user_documents` | **yes** | `{query: str}` — refused without a principal |

## Auth

The MCP boundary is the **only** auth surface — internal `source="internal"` callers bypass
it. Tokens are verified by the shared `services.app.auth_deps.decode_bearer_any` (local HS256
+ Supabase), turned into a `Principal(source="mcp")` in [`auth.py`](auth.py), and threaded
into every `tools/call`. An ASGI step verifies the token per request and binds the principal
for that request's scope before the session manager runs.

| Token | Result |
|-------|--------|
| absent | anonymous — `identity` / `search_medical_knowledge` work; `search_user_documents` refused |
| valid | `Principal(user_id=…, source="mcp")` — sees only that user's chunks |
| present but invalid | **rejected** — HTTP `401` before any tool runs |

## Connect an MCP host

Point any streamable-HTTP MCP client at `http://localhost:8090/mcp` and send
`Authorization: Bearer <jwt>` to exercise `search_user_documents`; with no header, that
tool returns a protocol error (principal required) while `identity` / `search_medical_knowledge`
still work. Scripted clients should use the trailing-slash form `…/mcp/` to skip the 307.

Mint a local test token with the same `AUTH_JWT_SECRET` the server runs with:

```python
import jwt, os
print(jwt.encode({"type": "local", "id": "demo-user"}, os.environ["AUTH_JWT_SECRET"], algorithm="HS256"))
```

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

## Tests

Tests run **inside** the CI stack, not against `la-mcp-server`. Both compose files are
required: the `.ci.yml` override runs `la-documedai` idle at `/workspace` with `PYTHONPATH`
set, so tests run via `exec` (the prod backend can't host tests).

```powershell
$C = "-f", "docker-compose.yml", "-f", "docker-compose.ci.yml"
docker compose @C up -d --build --wait la-qdrant la-mongo la-redis
docker compose @C up -d --build la-documedai

# protocol conformance only
docker compose @C exec -T la-documedai pytest -q ci_tests/integration/mcp/test_mcp_server.py

# full suite: Tool Core contracts, principal isolation, observability, cross-surface equivalence
docker compose @C exec -T la-documedai pytest -q ci_tests/integration/tool_core ci_tests/integration/mcp
```

`test_mcp_server.py` starts the real HTTP app on a loopback uvicorn and drives the JSON-RPC
lifecycle with the official MCP SDK **client** end to end — transport, not a stub. It covers
tools/list (3), tools/call identity, no-principal refusal, and a garbage bearer rejected with
`401`. A *successful* `search_user_documents` needs seeded Qdrant + LLM keys, so that path
lives in the isolation tests under
[`ci_tests/integration/tool_core/`](../../ci_tests/integration/tool_core/) instead.

The cross-surface equivalence test is the one that matters architecturally: it asserts the
internal and MCP surfaces return **identical content** for the same tool call.

## Observability

Live server-side metrics — two families, both on the default Prometheus registry and served
at `http://localhost:8090/metrics/`:

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

- Prometheus: `http://localhost:9090` (scrapes `la-mcp-server` + `la-llmguard`).
- Grafana: `http://localhost:3000` (anonymous admin) → **DocuMedAI — MCP Observability**:
  per-tool P50/P95/P99, MCP success rate by method, internal-vs-MCP overhead, error taxonomy.

Panels stay flat until traffic flows — run either [benchmark](#performance) (or drive the
server from an MCP host) to populate them.

## Performance

Two benchmarks measure two orthogonal things: **per-call protocol cost** (what does wrapping
the core in MCP cost?) and **serving capacity** (how much load holds, and how does it fail?).
Both write a JSON report (`--out`) and inherit the coordinated-omission discipline of
`vector_database_tests/` — latency is charged from each request's *ideal* send time, so a
saturated server shows rising latency (the truth) rather than reduced load (the lie).

Both drive `identity`, which returns a constant string and does no I/O. That is deliberate:
it is the **smallest possible denominator**, so these numbers are the protocol/serving layer
in isolation, never RAG or LLM cost. No documents or LLM keys required.

### 1. Protocol overhead — `overhead_benchmark.py` (loopback)

Times the same tool+args in-process (`call_tool`) and over streamable-HTTP
(`ClientSession.call_tool`), single call in flight. Run on loopback so network RTT ≈ 0 and
the delta isolates serialization + JSON-RPC + framing.

```powershell
docker compose exec la-mcp-server python -m mcp_server.overhead_benchmark --n 10000 --qps 50 --out overhead.json
```

**Measured** — n=10,000 per arm, 50 qps, loopback, tool `identity`
([`benchmark/overhead.json`](benchmark/overhead.json)):

| | P50 | P95 | P99 |
|---|---|---|---|
| direct (in-process) | 0.901 ms | 1.732 ms | 5.687 ms |
| mcp (streamable-HTTP) | 5.698 ms | 12.849 ms | 20.850 ms |
| **overhead** | **+4.797 ms** (+532%) | **+11.117 ms** (+642%) | **+15.163 ms** (+267%) |

Reading this honestly:

- **The absolute number transfers; the percentage does not.** ~4.8 ms p50 is the cost added to
  any tool. The percentage only looks extreme because `identity` does nothing — divide the same
  4.8 ms into a tool that does real work and the ratio collapses. Quoting +532% without that
  caveat would be dishonest in the flattering direction *and* the alarming one.
- **For scale**, Qdrant search at recall@10 ≥ 0.95 is 4.383 ms median
  ([`sweep_results/qdrant.json`](../vector_database_tests/sweep_results/qdrant.json)) — so
  protocol cost is the *same order* as the vector search it wraps. Rerank and LLM latency are
  **not yet measured**, so the share of a full RAG call is unknown and is not claimed here.
- **p99 overhead % (267%) is lower than p50 (532%)** because the direct arm's own p99 degrades
  (0.9 → 5.7 ms). The denominator moves; the ratio is not a stable figure of merit.
- Percentiles are reported at p95/p99, not just median, because framing and serialization
  jitter is worst at the tail.

### 2. Serving capacity — `loadtest_mcp.py` (two VMs)

Open-loop QPS ladder over N persistent sessions. Per rung: `achieved_rps` vs target (the
saturation signal), P50/P95/P99 under load, success rate, and an outcome breakdown
(`success` / `tool_error` / `transport_error`). Run client→server **across machines** — see
[`docs/benchmarks/mcp-procedure.md`](../../docs/benchmarks/mcp-procedure.md) for the full two-VM setup.

```bash
python -m mcp_server.loadtest_mcp \
    --url http://<server-internal-ip>:8090/mcp/ \
    --qps 25 50 75 100 150 200 400 800 \
    --duration 30 --warmup 5 --concurrency 16 --out capacity.json
```

**Measured** — GCP, two VMs same VPC/zone (server 4 vCPU / 16 GB, client 8 vCPU), internal IP,
30 s/rung, concurrency 16 ([`benchmark/capacity.json`](benchmark/capacity.json)):

| target qps | achieved rps | P50 | P95 | success | |
|---|---|---|---|---|---|
| 25 | 24.95 | 11.2 ms | 13.8 ms | 1.00 | healthy |
| 50 | 49.93 | 11.4 ms | 13.9 ms | 1.00 | healthy |
| 75 | 74.87 | 11.0 ms | 13.2 ms | 1.00 | healthy |
| 100 | 99.82 | 10.7 ms | 44.5 ms | 1.00 | healthy |
| 150 | 149.72 | 12.8 ms | 584.6 ms | 1.00 | knee |
| 200 | 158.71 | 3,566 ms | 6,139 ms | 1.00 | saturated |
| 400 | 166.67 | 8,922 ms | 16,950 ms | 1.00 | plateau |
| 800 | 162.32 | 12,237 ms | 23,162 ms | 1.00 | plateau |

**~150 rps sustained at ~13 ms median; saturation at ~160–180 rps.**

The overload rungs are the point of running them: from 200 → 800 qps — a **4× overload** —
throughput pins flat at 159–167 rps with **`success_rate` 1.00 and zero transport errors at
every rung**. The server degrades by queueing, never by failing. Latency, not error rate, is
the saturation signal, which is exactly what open-loop measurement is for.

> **Tail caveat.** P99 stays 280–460 ms at the healthy rungs even where P95 is ~13 ms. A 60 s
> control run at 10 qps / concurrency 8 showed P95 13.2 ms and P99 20.7 ms, so that thin tail
> (~1 %) is residual client-side scheduling, not a server stall. Treat P50/P95 as the load
> numbers and read P99 with this in mind.

### Calibrating the load generator (why concurrency 16)

The load generator is single-process asyncio; each worker holds a persistent SSE session. At
high `--concurrency` the **client** becomes the bottleneck and you measure it instead of the
server. This was not hypothetical — it was caught mid-run and corrected:

| concurrency | 25 qps P95 | 200 qps achieved | 150 qps P95 |
|---|---|---|---|
| 64 | 522 ms | 178.5 rps | — |
| 32 | — | 172.8 rps | 1,415 ms |
| **16** | **13.8 ms** | 158.7 rps | **584 ms** |

Dropping 64 → 16 improved P95 at low load by **38×** (522 → 13.8 ms) with identical throughput:
idle sessions were adding pure scheduling latency, charged from ideal send time. Saturation
holds at ~160–180 rps across all three settings, so the *ceiling* is genuinely server-side —
but only the concurrency-16 numbers are clean enough to publish.

Before trusting any ladder, run the two-step gate:

1. **Harness correct** — one low rung (`--qps 20 --duration 10 --concurrency 8`); require
   `achieved ≈ target` and `success_rate` 1.0. If not, auth or transport is broken; do not sweep.
2. **Server-bound, not client-bound** — re-run one mid rung with `--concurrency` doubled. If
   `achieved_rps` rises, the client was the limit: raise client vCPU and redo.

**Known-unexplained:** why the ceiling sits at ~170 rps has not been isolated — CPU saturation
on 4 vCPU vs. contention in the streamable-HTTP session manager. `docker stats` on
`la-mcp-server-service` during a 400 qps rung distinguishes them (~400 % = CPU-bound). If it is
contention rather than CPU, `json_response=False` in [`__main__.py`](__main__.py) — which forces
SSE framing on every unary reply — is the first thing to test. Not yet measured, so not claimed.
