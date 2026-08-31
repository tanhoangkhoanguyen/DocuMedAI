# MCP Load Test Runbook (two-VM, GCP)

Standing up the serving-capacity benchmark for `backend/mcp_server/loadtest_mcp.py`.
Results and interpretation live in [`backend/mcp_server/README.md`](../../backend/mcp_server/README.md#2-serving-capacity--loadtest_mcppy-two-vms).

The load test drives `identity` only — a constant string, no RAG, no LLM — so it measures the
**protocol/serving layer**, not retrieval. No documents or LLM keys needed on either machine.

**Shape of the job:** two GCP VMs (§1–2) → open port 8090 between them (§3) → server up (§4)
→ client ready (§5) → **prove the harness isn't lying** (§6) → sweep and read (§7–8).
Budget ~45 min the first time, most of it VM creation and the Docker build. §6 is the step
people skip and the reason most published benchmarks are wrong.

## Why two machines

Open-loop measurement charges latency from each request's *ideal* send time, so it needs a
real NIC in the path. Loopback would measure a network that isn't there. Two VMs in the same
VPC and zone, driven over **internal IP**, give sub-millisecond stable RTT while still crossing
a real interface — a latency rise then means the server saturating, not internet jitter.

## The trap: sizing the client

`loadtest_mcp.py` is single-process asyncio. Per call it parses an SSE stream; per worker it
signs a JWT. **An undersized client saturates before the server and you publish your client's
limits as the server's.** Give the client more CPU than the server, and always run the gate in
step 6.

## 1. Create two VMs

Compute Engine → Create instance. Both in the **same zone and VPC**.

| | server | client |
|---|---|---|
| name | `mcp-server` | `mcp-client` |
| OS | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| vCPU | 4 (8 if driving real RAG tools) | 8 |
| RAM | **≥ 16 GB** | 4 GB |
| network tag | `mcp-server` | `mcp-client` |

Server RAM is the hard constraint, not CPU: `la-mcp-server` has `mem_limit: 8g` and loads the
reranker + embedding model at import even though `identity` never uses them.

The client needs no Docker and no models — `loadtest_mcp.py` imports only `mcp` and `jwt`.

Add the network tags at create time (Console → Networking → Network tags). Verify they applied
— an untagged VM silently matches no firewall rule, which is the most common cause of a hang:

```bash
gcloud compute instances list --format="table(name,networkInterfaces[0].networkIP,zone,tags.items)"
```

`ITEMS` must be non-empty for both. Note the server's **internal** IP (e.g. `10.128.0.10`).

## 2. Docker on the server

Follow [DEPLOYMENT.md](../deployment.md) §2, then re-login so the `docker` group applies.

```bash
docker --version && docker compose version
```

## 3. Firewall: TCP 8090, client → server only

`/mcp` serves `identity` and `search_medical_knowledge` **anonymously**, so an open port is a
live endpoint. Scope the rule; never `0.0.0.0/0`.

```bash
gcloud compute firewall-rules create allow-mcp-8090-from-client \
  --network=default --direction=INGRESS --action=ALLOW \
  --rules=tcp:8090 --source-tags=mcp-client --target-tags=mcp-server
```

Confirm the tags match exactly — a typo in `--target-tags` matches nothing and looks identical
to a network failure:

```bash
gcloud compute firewall-rules list --filter="name~mcp" \
  --format="table(name,sourceTags.list(),targetTags.list(),allowed[].map().firewall_rule().list())"
```

## 4. Start the server

```bash
git clone https://github.com/tanhoangkhoanguyen/DocuMedAI.git && cd DocuMedAI
```

`AUTH_JWT_SECRET` must be **identical** to the client's in step 5 — the client mints HS256
tokens the server verifies. Use ≥ 32 bytes to avoid PyJWT's `InsecureKeyLengthWarning`:

```bash
cat > .env <<'EOF'
AUTH_JWT_SECRET=replace-with-a-long-shared-secret-at-least-32-bytes
UUID_NAMESPACE="b1343753-a4f3-4be8-94c6-9780ee37ab61"
EOF

docker compose up -d --build la-mcp-server la-qdrant la-mongo la-redis
docker compose logs -f la-mcp-server     # wait for: serving streamable-HTTP at http://0.0.0.0:8090/mcp
```

Verify locally. **The trailing slash is required** — `Mount` 307-redirects `/metrics`, and
plain `curl` does not follow redirects, so the slashless form returns empty:

```bash
curl -s http://localhost:8090/metrics/ | head -3
```

Note the container healthcheck is only a TCP liveness probe, so `healthy` means the port is
open — not that the app responds. Trust this curl, not `docker ps`.

## 5. Prepare the client

```bash
sudo apt update && sudo apt install -y python3-pip git
git clone https://github.com/tanhoangkhoanguyen/DocuMedAI.git
cd DocuMedAI/backend
pip install "mcp==1.26.0" "PyJWT==2.13.0"

export AUTH_JWT_SECRET=replace-with-a-long-shared-secret-at-least-32-bytes   # same as step 4
export PYTHONPATH=$PWD                                                       # run from backend/
```

Reachability, with the server's internal IP and trailing slash:

```bash
curl -s http://10.128.0.10:8090/metrics/ | head -3
```

Prometheus text = through. Timeout = firewall/tags/IP (step 3). Note `export`s die with the
SSH session — re-run them after reconnecting.

## 6. The gate — run before any ladder

Two checks. Skip them and the sweep produces numbers about your client.

**6a — harness and auth correct:**

```bash
python3 -m mcp_server.loadtest_mcp --url http://10.128.0.10:8090/mcp/ \
  --qps 20 --duration 10 --warmup 3 --concurrency 8
```

Require `achieved ≈ 20` **and** `success_rate=1.0`. All-`transport_error` means the
`AUTH_JWT_SECRET` differs between VMs — the single most common failure.

**6b — server-bound, not client-bound:** re-run one mid rung with concurrency doubled.

```bash
python3 -m mcp_server.loadtest_mcp --url http://10.128.0.10:8090/mcp/ --qps 200 --duration 20 --concurrency 16
python3 -m mcp_server.loadtest_mcp --url http://10.128.0.10:8090/mcp/ --qps 200 --duration 20 --concurrency 32
```

Roughly flat `achieved_rps` → server-bound, numbers are real. Rising → the client was the
limit; raise client vCPU and redo. Watch `top` on the client: a generator pinned at 100 % CPU
is measuring itself.

Note that *higher* concurrency can make tail latency dramatically worse while throughput holds
(idle SSE sessions add scheduling latency). Pick the **lowest** concurrency that still reaches
saturation — 16 for this workload. See the calibration table in the README.

## 7. Run the ladder

```bash
python3 -m mcp_server.loadtest_mcp \
  --url http://10.128.0.10:8090/mcp/ \
  --qps 25 50 75 100 150 200 400 800 \
  --duration 30 --warmup 5 --concurrency 16 \
  --out capacity.json
```

Keep the overload rungs (400/800). Past saturation they add no throughput information, but
they are the only evidence of *degradation mode* — whether the server queues gracefully or
collapses. They run long, since the queue drains slowly at multi-second latency.

```bash
mv capacity.json ~/DocuMedAI/backend/mcp_server/benchmark/
```

## 8. Read it

- **Sustainable throughput** = highest rung where `achieved_rps ≈ target` *and* the tail stays
  bounded. The first rung falling short is the **saturation point**.
- **A latency cliff with `success_rate` 1.0 is not an error** — requests queued and were charged
  from ideal send time, exactly what a real client blocked on a slow server experiences.
- **Outcomes:** `transport_error` spiking = resets/timeouts; `tool_error` = the tool returned
  `isError`; all-`transport_error` from the start = secret mismatch.
- Compare against [`throughput_results/qdrant.json`](../../backend/vector_database_tests/throughput_results/qdrant.json),
  the same open-loop method on a different component, for the shape of a healthy cliff.

Optional live view, on the server:

```bash
docker compose --profile observability up -d la-prometheus la-grafana   # Grafana :3000
docker stats --no-stream la-mcp-server-service                          # during a 400 qps rung
```

`docker stats` is what distinguishes CPU saturation (~400 % on 4 vCPU) from session-manager
contention — currently unresolved for this server.

## Failure modes, by likelihood

| Symptom | Cause |
|---|---|
| Every call `transport_error`, `success_rate` 0 | `AUTH_JWT_SECRET` differs between VMs |
| `KeyError: 'AUTH_JWT_SECRET'` | not exported on the client (`loadtest_mcp.py:52`) |
| `ModuleNotFoundError: mcp_server` | not running from `backend/`, or `PYTHONPATH` unset |
| `curl` hangs then times out | firewall rule missing, tags unapplied/typo'd, or external IP used instead of internal |
| `curl` returns empty, exit 0 | missing trailing slash — `/metrics/` and `/mcp/` both 307 |
| Low `achieved_rps` even at 50 qps | client-side bottleneck — back to 6b |
| Huge P95 at low load, normal median | concurrency too high — lower `--concurrency` |
