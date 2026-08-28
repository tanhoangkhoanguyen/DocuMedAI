# bench/ — open-loop load driver

`load.js` drives the gateway at a fixed arrival rate and reports what it did.
Requires the `multi-replica` profile up (nginx on 8082, replicas, mock upstream).

```bash
# from backend/llmguard/
docker run --rm -i --network documedai_documedai-net \
  -e BASE_URL=http://la-nginx:80 -e PROVIDER=mock -e QPS=20 -e DURATION=30s \
  grafana/k6:latest run - < bench/load.js

# archive the numbers with the config that produced them
docker run --rm -i --network documedai_documedai-net \
  -e BASE_URL=http://la-nginx:80 -e PROVIDER=mock -e QPS=20 -e DURATION=30s \
  -e SUMMARY_JSON=1 -v "$PWD/bench:/out" -w /out \
  grafana/k6:latest run - < bench/load.js     # -> bench/bench-summary.json
```

Knobs: `BASE_URL`, `PROVIDER`, `MODEL`, `MODE`, `QPS`, `DURATION`, `STREAM`,
`MAX_VUS`, `SUMMARY_JSON`, `VERTEX_URL`/`VERTEX_TOKEN` (vertex-direct mode). The
network name carries the Compose project prefix — check `docker network ls` if
it differs. On Git Bash, export `MSYS_NO_PATHCONV=1` first: path conversion
mangles `-v` mounts into junk `;C` directories.

Traces land in ClickHouse when `OTEL_EXPORTER_OTLP_ENDPOINT` is set in `.env`
(observability profile up) — `quantileExact` over root spans is the latency
store; the CSV carries only what spans cannot (gauge occupancy per second).

Two sidecars:

- `watch-metrics.sh` — every replica's `llmguard_in_flight` + refusal counters,
  1/s, to CSV. k6 sees status codes, not server state, and `/metrics` through
  nginx round-robins across per-replica registries — replicas are scraped
  directly instead.
- `sweep.sh` — one k6 run per QPS step, watcher alive alongside, each step
  archived as a directory. `STOP_ON_429=1` stops at the first 429 (C2). A step
  whose driver fell behind stops the sweep: higher steps would lie.

## Scenarios and where each number comes from

Every variable must have a source: a config constant, a statistical rule, or a
measurement from an earlier scenario. The one guess (`MAX_VUS`) has a tripwire.

| | Upstream | Shape | Key derivations |
|---|---|---|---|
| C1 overhead | Vertex | QPS 1, n=200, both arms | n=200 from the quantile rule (≥ 10/(1−q) samples ⇒ p95 needs 200) — so C1 reports p50/p95, never p99 |
| A capacity | mock | ladder ×2, 60s steps | `MOCK_LATENCY` = **C1's measured p50** (Little's law: the 256 ceiling is unreachable with a ~ms mock); 60s ≥ 3× `CIRCUIT_OPEN_FOR`; `RATE_LIMIT_RPM` raised out of the way, or the ladder measures the limiter |
| B overhead | mock | QPS 20, 60s | 20 × 60s = 1200 ≥ 1000 samples for p99; must sit far below A's measured knee |
| C2 ceiling | Vertex | ladder 1→13, `STOP_ON_429=1` | route `rpm` raised first, or the first 429 is our own; one run with `RETRY_MAX=1` to see Google's raw 429s, one with the default to measure retry amplification (bound: ×`RetryMax`) |
| D alerts | mock | error rate 0.3 / 0.8 | both sides of `CircuitFailRatio=0.6`; QPS ≥ `CircuitMinReqs` or the breaker never has enough samples |

Run order is a data dependency, not a preference: **C1 → A → B → C2 → D**.

Vertex has no fixed QPS to find: pay-as-you-go uses dynamic shared quota, so
C2's first-429 point is an observation of one moment, not a constant — the
useful outputs are whether the breaker stayed closed on quota 429s and what
retry amplified, not the number itself.

One mechanism per scenario: anything that is not the object under test is moved
out of the path by config, and the summary's config block records what was set.

## Why open-loop

`constant-arrival-rate` starts an iteration on a **schedule**, not when the last one
finishes. A closed-loop driver (a curl loop, or `ramping-vus`) sends the next request
only after the previous reply lands, so when the gateway slows down the driver slows
with it: offered load quietly drops and the tail latency being measured never
appears. That is coordinated omission, and it makes an overloaded system look
healthy.

`dropped_iterations` is the driver admitting it fell behind, so it is asserted at
`count==0`. A run with drops did not offer the QPS it claims.

## Why `served_duration` and not `http_req_duration`

k6's own `http_req_duration` times **every** response, including the 429s a shedding
gateway returns in microseconds. Measured here at 40 QPS against `MAX_IN_FLIGHT=2`:

| | median |
|---|---|
| `http_req_duration` (all responses) | **1 ms** |
| `served_duration` (200s only) | **803 ms** |

87% of requests were shed. The default metric reports the gateway got 800x faster at
the moment it started failing — the same distortion that made
`llmguard_request_duration_seconds` useless and got it dropped in Phase 4.
`served_duration` times only what was actually served; `shed_rate` carries the
refusals separately, as a result — finding where shedding starts is what the
benchmark is for. The only asserted threshold is `dropped_iterations == 0`,
which is about the driver, not the gateway.

## Arms

- gateway → mock: the default.
- direct → mock: `BASE_URL=http://la-mockupstream:8090` — same body, no gateway.
- direct → Vertex: `MODE=vertex-direct` with `VERTEX_URL` (the full
  `:generateContent` URL) and `VERTEX_TOKEN` (`gcloud auth print-access-token`,
  lives ~1h). In this mode a 429 is Google's, not a shed — the summary's
  `config.MODE` keeps the two apart.
- gateway → Vertex: the default arm against the single-replica stack
  (`BASE_URL=http://la-llmguard:8081`, `PROVIDER=vertex`).

`ttft` records time-to-first-byte for streamed 200s only — through nginx this
is the number `proxy_buffering off` exists to protect.

## Not yet done

The LiteLLM arm, the three-arm runner and the charts are Issue 6.2 / 6.3.
Published numbers come from the two-VM GCP setup (driver and stack on separate
machines, same region as Vertex); local runs are for finding bugs in the
method, not for RESULTS.
