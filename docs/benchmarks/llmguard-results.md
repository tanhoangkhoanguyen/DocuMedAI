# Benchmark results

**Provisional — local, single machine.** Driver and stack shared one 12-core
Windows laptop, so any number where the driver was itself under load belongs to
the driver, not the gateway. Scenario A's ceiling is exactly that case and is
labelled below. Published numbers need the two-VM GCP setup.

Run at commit `fe83231`, Docker 28.3.2, `multi-replica` profile (2 replicas +
nginx + mockupstream + Redis + ClickHouse). Upstream is `mockupstream` with
`MOCK_LATENCY=2000ms` — a stand-in for a real model's think time, without which
`MAX_IN_FLIGHT` is unreachable at any QPS a driver can offer.

Steps and commands: [llmguard-procedure.md](llmguard-procedure.md).

## A — Capacity

`RATE_LIMIT_RPM` raised out of the way, so the ceiling under test is admission
control (256 per replica), not the token bucket.

| offered QPS | shed | p50 | p99 | dropped |
|---|---|---|---|---|
| 20 | 0% | 2004ms | 2007ms | 0 |
| 40 | 0% | 2004ms | 2034ms | 0 |
| 80 | 0% | 2004ms | 2008ms | 0 |
| 160 | 0% | 2008ms | 2155ms | 0 |
| 320 | — | — | — | **driver collapsed** |

Clean to 160 QPS: nothing shed, and p99 sat 150ms over the upstream's own
latency. At 160 QPS × 2s that is ~80 in-flight per replica, under a third of the
ceiling — so **the gateway's own limit was never reached**.

320 QPS failed as connection resets and nginx 499s (client hung up) within one
second, not as 429s. That is the k6 container running out of room on the same
host, and it is why the real ceiling needs a separate driver machine.

## B — Overhead

Same upstream both arms, 20 QPS × 60s, 1200 samples each.

| | direct to mock | through gateway | overhead |
|---|---|---|---|
| p50 | 2001.1ms | 2003.7ms | **+2.6ms** |
| p95 | 2001.8ms | 2006.1ms | **+4.3ms** |
| p99 | 2002.1ms | 2009.1ms | **+7.0ms** |

nginx + admission + rate limiter + breaker + provider translation cost ~3ms at
p50, 7ms at p99 — **0.13% of a 2s call**.

## C — Vertex

Abandoned as a baseline. Pay-as-you-go uses dynamic shared quota, and at **1 QPS**
Vertex refused 66% of requests on one run and 19% on another, both
`RESOURCE_EXHAUSTED`. There is no ceiling to find and no reproducible latency to
compare against; every number would describe Google's global load at that
minute. The mock is the upstream for anything measured.

## D — Circuit breaker

| mock error rate | vs `CircuitFailRatio=0.6` | `circuit_state` |
|---|---|---|
| 30% | under | no series — never opened |
| 80% | over | 2 (open), including after 1200 healthy requests first |

The second row only passes since the `Interval` fix below.

## Streaming

24 frames × 120ms `MOCK_CHUNK_DELAY`, measured with `curl`:

| | ttfb | total |
|---|---|---|
| `proxy_buffering on` | 2.003s | 4.894s |
| `proxy_buffering off` | 2.006s | 4.898s |

Indistinguishable. See the finding below.

## Cross-replica breaker flag

One replica was driven to trip; a second replica that had served **zero**
requests then got 12, with and without the flag in Redis.

| 12 requests to the untouched replica | with flag | flag deleted |
|---|---|---|
| its own `circuit_state` | no series — never tripped | 2 |
| upstream calls | none, refused on arrival | ~10 requests x 4 attempts |
| wall time | immediate | ~10.3s per request until it tripped |

The flag saves a replica from re-learning an outage a peer already found. It is
**not** a push: `openElsewhere` is checked on the request path, so an idle
replica never changes state — it refuses the first request it receives and
that is the whole mechanism. Publishing was observed ~9.5s into a 20 QPS run at
an 80% error rate, TTL 20s (`CIRCUIT_OPEN_FOR`).

## Retry

From ClickHouse spans, under injected failures: **3.38 `upstream.attempt` spans
per trace**, and a request that exhausts all four attempts takes **~10.3s** —
`RETRY_BASE_DELAY=300ms` doubling to `RETRY_MAX_DELAY=8s`. Worth knowing before
tuning `CIRCUIT_MIN_REQUESTS`: at that pace a breaker needs ~100s of failures to
collect ten observations at low traffic.

## Findings

**1. The breaker lost the ability to open as uptime grew.** `gobreaker`'s zero
`Interval` means "never reset the counts while closed", making the failure ratio
a lifetime average. A replica with ~17k healthy requests behind it needed ~25k
failures to reach 0.6: an 80% error rate left it closed for two minutes straight,
while the same load tripped it in 15 requests on a freshly restarted replica.
Longer uptime meant less protection. Fixed in `23eb8e4` with a 60s window; the
window must exceed the time to collect `CircuitMinReqs` observations, or every
request lands in a fresh generation and `Requests` never reaches the minimum.

**2. The collector was silently reading zero.** `watch-metrics.sh` matched metric
names with `"^"m"($|{)"`, a pattern that has to survive both shell and awk
quoting and matches nothing when it does not. Every `in_flight` sample recorded
as 0, which reads as an idle gateway rather than as an error. Fixed in `aa8e86d`.

**3. `proxy_buffering off` is insurance, not a fix.** The nginx template called it
"THE LOAD-BEARING LINE" on the theory that nginx would otherwise deliver a
generation as one late blob. Nothing here reproduces that: the upstream flushes
each frame of a chunked `text/event-stream`, and `on` and `off` differ by 3ms.
Kept, described as insurance (`34ee3df`).

**4. `ttft` was not measuring TTFT.** k6 has no streaming reader, so the trend
recorded `res.timings.waiting` — time to the response *header*, which a proxy
forwards before deciding anything about the body. Renamed `ttfb_header`; real
TTFT needs `curl -N` or `time_starttransfer`.

## Not measured

- **The gateway's actual capacity.** Needs the driver on its own machine.
- **nginx tuning** (`worker_connections`, `keepalive`, `max_fails`,
  `fail_timeout`) — still the unmeasured defaults the template says they are.
  `NGINX_FAIL_TIMEOUT=10s` is shorter than `CIRCUIT_OPEN_FOR=20s`, so nginx
  re-admits traffic to a replica whose breaker is still open; untested.
- **Alert thresholds** in `observability/alerts.yml`, still starting points.
- **Cross-replica breaker propagation** under load — covered by tests, not by a
  benchmark.
