# Benchmark results

Two GCP VMs in `us-central1-a`, same VPC, driven over internal IP: **stack**
`e2-standard-4` (4 vCPU) running the `multi-replica` profile (2 replicas + nginx
+ mockupstream + Redis + ClickHouse), **driver** `e2-standard-8` (8 vCPU) running
k6 alone. Separate machines because the local ladder collapsed into nginx 499s at
320 QPS — that was the driver running out of room on the stack's host, not the
gateway refusing.

Run at commit `29415a0`, Docker 28.5.2. Upstream is `mockupstream` with
`MOCK_LATENCY=2000ms` — a stand-in for a real model's think time, without which
the in-flight ceiling is unreachable at any QPS a driver can offer.

Scenario B, D, streaming, the cross-replica flag and retry were measured locally
on a 12-core Windows laptop before the move; they compare two arms of the same
run or read a state transition, so the host does not enter the result. Anything
that depends on absolute capacity was re-measured on GCP and says so.

Steps and commands: [llmguard-procedure.md](llmguard-procedure.md) · GCP setup:
[llmguard-gcp.md](llmguard-gcp.md).

## Capacity — where shedding starts

`RATE_LIMIT_RPM` raised out of the way, so the ceiling under test is admission
control, not the token bucket. `MAX_IN_FLIGHT` lowered from its 256 default to
**64 per replica** — 128 slots across two replicas, or 64 req/s at a 2s upstream.
At the default the saturation point sits above what one driver VM can offer (see
below), and a ceiling that is never reached measures nothing.

45s per step, `dropped=0` and zero 5xx throughout.

| offered QPS | shed | **goodput** | p50 served | p95 | p99 |
|---|---|---|---|---|---|
| 40 | 0.0% | 40.0 | 2003.8ms | 2004.8ms | 2005.7ms |
| 80 | 20.2% | **63.8** | 2003.7ms | 2004.6ms | 2005.3ms |
| 160 | 59.1% | **65.4** | 2003.5ms | 2004.4ms | 2005.0ms |
| 320 | 79.6% | **65.3** | 2003.3ms | 2004.5ms | 2005.8ms |

Goodput is `QPS × (1 − shed_rate)`: what the gateway actually served. It
**plateaus at ~65 req/s while offered load grows 8x**, against a predicted 128
slots ÷ 2s = 64 — within 2% of Little's law. Overload does not degrade it; the
curve flattens rather than falling over.

The latency column is the point. p99 for served requests is **2006ms at 0% shed
and 2006ms at 80% shed** — refusing four requests in five costs an admitted
request nothing. A 429 is returned before a slot is taken, so shedding is work
the gateway does not do.

Occupancy confirms the mechanism rather than inferring it. Scraping one replica
directly at 320 QPS, `llmguard_in_flight` held **64/64 for 16 seconds** (two
samples at 63, a slot between release and reissue) — the semaphore is at its
bound, not near it, and no slot leaked.

At the 256 default the same driver could not reach the knee: 640 QPS left
`in_flight` at 253–254 with **0% shed**, and 1280 QPS dropped 10,431 iterations
on the driver — a driver limit, so the step says nothing about the gateway. The
ceiling is a latency-for-goodput trade (256 slots ÷ 2s admits more than the
upstream serves, so the queue shows up as latency instead of as a 429), which is
why it is a per-deployment number rather than one worth publishing as a default.

## Gateway overhead vs direct

Same upstream both arms, 20 QPS × 60s, 1200 samples each. Measured
locally: both arms share a host, so the difference is the gateway's.

| | direct to mock | through gateway | overhead |
|---|---|---|---|
| p50 | 2001.1ms | 2003.7ms | **+2.6ms** |
| p95 | 2001.8ms | 2006.1ms | **+4.3ms** |
| p99 | 2002.1ms | 2009.1ms | **+7.0ms** |

nginx + admission + rate limiter + breaker + provider translation cost ~3ms at
p50, 7ms at p99 — **0.13% of a 2s call**.

## Resilience through an outage

20 QPS x 90s against the multi-replica stack, with an **8s total outage** opened
on the mock 30s in. At 20 QPS the window covers ~160 requests, 8.9% of the run.

| | client error rate | p50 | p99 | attempts/request |
|---|---|---|---|---|
| through the gateway | **6.22%** | 2003.5ms | **4283.6ms** | 1.25 |

Retry rescued roughly **a third** of the requests that would otherwise have
failed, and the price is visible only in the tail: p50 is unchanged while p99
doubles to 4.28s. Those are the rescued requests, each carrying one extra
backoff. Retry trades errors for latency, and only at the tail.

The breaker stayed **closed** throughout — no `circuit_state` series on either
replica. Correct: 8s of failures cannot reach `CircuitFailRatio=0.6` inside a
60s window, and a short outage is what retry is for. Tripping here would have
converted a recoverable blip into a self-inflicted one.

### The 20%-error-rate arm measures nothing — do not run it

The obvious way to run this scenario is `MOCK_ERROR_RATE=0.2`, and it produces a
flat contradiction:

| | client error rate | attempts/request |
|---|---|---|
| direct to mock | 20.98% | 1.0 |
| through the gateway | **21.75%** | **1.65** |

The gateway did 783 extra upstream calls and rescued **nothing**. That is not a
retry bug: `mockupstream` derives its failure verdict from
`FNV-1a(method, path, body, config, nonce)`, and a retry replays the body
byte-for-byte — as it must, since a retry that alters the request is not a retry.
Same bytes, same verdict, four times. The mock cannot express a *transient*
failure this way, and transient is the only kind retry can address.

Read the attempt count before believing any resilience number. An arm where
`attempts/request > 1` and the error rate did not move is measuring the harness,
not the gateway. Use the wall-clock outage window instead, which is independent
of request content.

## The MAX_IN_FLIGHT formula, against real inputs

`config.go` derives the ceiling as `(RateLimitRPM / 60) x p95_upstream_seconds x
1.5`, and calls 256 "that formula at 480 RPM and a 20s p95". Both inputs were
assumptions. Measured from the stack VM, calling Vertex directly over ADC
(`gemini-2.5-flash`, us-central1, one request per second, n=56 successes):

| | p50 | p90 | p95 | max | refused |
|---|---|---|---|---|---|
| Vertex `generateContent` | 4.72s | 8.30s | **11.03s** | 19.63s | 4/60 (DSQ 429s) |

So the assumed **20s p95 is closer to the measured max** than to the p95 — the
worst case had been used as the typical one. And the RPM input is wrong in the
other direction: the formula uses the global `RATE_LIMIT_RPM=480` default, but
the route that actually serves this model is budgeted at `rpm: 200` in
`config.yaml`. On real inputs the formula gives

    (200 / 60) x 11.0 x 1.5 = 55

against the 256 that ships — 4.6x lower, both inputs having erred the same way.

**256 stays.** Not because the formula is wrong but because it is a floor, not a
target: the formula's job is to guarantee a bucket-legal burst is never shed, and
exceeding it costs idle memory, while falling short sheds requests the gateway
could have served. What a high ceiling actually costs was measured directly —
latency, not collapse (see Capacity) — and the tail here is the reason to keep the
headroom: a 19.6s max against an 11s p95 means slow generations are real, and a
ceiling sized to p95 would shed exactly when the model is slowest, which is when
shedding is least defensible.

Caveats that keep this from being the last word: n=56 supports a p50 but not a
p95 (the quantile rule wants 200+), and 6.7% of requests were refused by dynamic
shared quota, so the surviving latencies are the ones DSQ let through.

## Vertex as a baseline — why it cannot be one

Abandoned as a baseline. Pay-as-you-go uses dynamic shared quota, and at **1 QPS**
Vertex refused 66% of requests on one run and 19% on another, both
`RESOURCE_EXHAUSTED`. There is no ceiling to find and no reproducible latency to
compare against; every number would describe Google's global load at that
minute. The mock is the upstream for anything measured.

## Circuit breaker trip threshold

| mock error rate | vs `CircuitFailRatio=0.6` | `circuit_state` |
|---|---|---|
| 30% | under | no series — never opened |
| 80% | over | 2 (open), including after 1200 healthy requests first |

The second row only passes since the `Interval` fix below.

## Tracing cost on the request path

Same rung both arms: 40 QPS x 60s (2400 samples, shed 0%), one with
`OTEL_EXPORTER_OTLP_ENDPOINT` unset, one with the collector up. Off is a genuine
no-op — the SDK is never installed — so this is the full cost of exporting every
span, not a sampling delta.

| | tracing off | tracing on | delta |
|---|---|---|---|
| p50 | 2003.8ms | 2003.8ms | **0.0ms** |
| p95 | 2004.9ms | 2004.9ms | **0.0ms** |
| p99 | 2005.9ms | 2005.9ms | **0.0ms** |

Identical to the tenth of a millisecond, which is close enough to be worth
distrusting — so the spans were counted rather than assumed. ClickHouse held
**7206 spans across 2402 traces**: 2401 k6 iterations plus one probe, three spans
each, none dropped. `requests_total` equalling `countDistinct(TraceId)` is the
control that makes a silent export failure visible, and it holds.

The delta is zero rather than small because export is **off the request path**:
the SDK batches spans and flushes them from its own goroutine. What the request
pays is span creation, measured by `make bench` at **~450ns** (no-op) to **~1020ns**
(SDK, recording) — against a 2s upstream, four to five orders of magnitude below
the signal. This is the measurement behind sampling 100% of traces instead of a
ratio: there is nothing here to sample away.

## nginx calibration — four defaults

Four numbers the template took as defaults. All four were measured on the GCP
stack and **all four were kept** — but three of them are now kept for a reason,
and one of the reasons overturns what the config comment claimed.

| | value | measurement | decision |
|---|---|---|---|
| `worker_processes` | 1 | **17.4%** of one core at 320 QPS | keep — ~6x of headroom |
| `worker_connections` | 1024 | peak **128** upstream connections (12%) | keep |
| `keepalive` | 32 | steady state **64** conns/replica; raising to 128 changed nothing | keep |
| `fail_timeout` | 10s | never interacts with `CIRCUIT_OPEN_FOR` | keep |

**`keepalive 32` is undersized and it does not matter.** At 160 QPS nginx held
128 established upstream connections — 2 replicas x 64 slots — against a pool of
32, so three of four requests could not reuse one. Raising `keepalive` to 128,
the full concurrency, moved p99 from **2006.0ms to 2006.3ms** and TIME-WAIT over
a 30s run from **593 to 584**. Both are noise. The pool is undersized against the
rule the comment stated, and the effect of fixing it is unmeasurable here,
because a 2s upstream dwarfs a TCP handshake on a same-VPC NIC.

**The connection churn is refusals, not the pool.** TIME-WAIT looked like the
symptom of a small pool, and it is not: it climbed ~20/s at a 60% shed rate and
sat flat at 0% shed, with `keepalive` making no difference at either. Each 429
costs a connection rather than returning one to the pool. Read TIME-WAIT as a
refusal counter, not a tuning signal.

**`fail_timeout=10s` vs `CIRCUIT_OPEN_FOR=20s` is not a conflict.** The two
windows look like they should match — nginx re-admitting a replica whose breaker
is still open — but they never meet: a tripped breaker answers **503**, and 503
is not in nginx's default `proxy_next_upstream` set (`error timeout`), so nginx
never counts it as a failure and never ejects a replica for having tripped. They
govern different events: nginx watches for a dead process, the breaker for a sick
upstream.

**Per-peer ejection works.** One `server` line naming a Compose service resolves
to one upstream entry per task IP, so `max_fails` marks a single replica down
rather than the whole pool. Stopping one replica: **10/10 requests returned 200**
through the survivor. Traffic splits evenly (40/40 established connections at 40
QPS).

## Streaming — time to first token

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

## Retry amplification

From ClickHouse spans, under injected failures: **3.38 `upstream.attempt` spans
per trace**, and a request that exhausts all four attempts takes **~10.3s** —
`RETRY_BASE_DELAY=300ms` doubling to `RETRY_MAX_DELAY=8s`. Worth knowing before
tuning `CIRCUIT_MIN_REQUESTS`: at that pace a breaker needs ~100s of failures to
collect ten observations at low traffic.

## Collector queue sizes, against real span volume

`otel-collector.yaml`'s batch and queue settings were "left at their defaults
until a real benchmark says what the load is". The load, measured at 320 QPS with
80% shedding for 60s:

| | |
|---|---|
| traces offered / stored | **19,201 / 19,201** — nothing dropped |
| spans stored | 26,881 (**448 spans/s**) |
| spans per trace | 1.40 |

3.7x the span rate of the tracing arm, still lossless, so `send_batch_size: 1024`
and the default sending queue stay. At 448 spans/s the batch fills in ~2.3s, so
the size cap binds before `timeout: 5s` and inserts land roughly every two
seconds.

The 1.40 spans/trace is a useful cross-check on something else: a shed request
produces one span (refused before dispatch) and a served one produces three, so
`0.2 x 3 + 0.8 x 1 = 1.4` exactly. Admission control is refusing before the
upstream call, confirmed from the trace store rather than inferred from latency.

The collector publishes no telemetry of its own here — no `service.telemetry`
block, and ports 8888/8889/13133 are closed — so batch and queue occupancy cannot
be read directly. The trace-count identity is the only available check.

## Alert thresholds, against the measurements

`alerts.yml` said its six thresholds were "STARTING POINTS, not measurements".
Checked against everything above, three were wrong and three hold.

| rule | was | now | why |
|---|---|---|---|
| `CircuitOpen` | `== 2` for **1m** | no `for` | the breaker half-opens after `CIRCUIT_OPEN_FOR=20s`, so the gauge cannot hold 2 for a minute — the alert could not fire on a single outage |
| `ErrorRateHigh` | > **10%** | > **5%** | a measured 8s total outage left clients at 6.2% after retry, under the old threshold |
| `InFlightHigh` | > 179, unexplained | > 179, explained | 179 is 70% of 256, and 256 is headroom over a formula that gives ~55 on measured inputs; the rule now says so |
| `Shedding` | `> 0` for 2m | unchanged | shedding is never normal, and served latency stays flat through it, so there is no rate worth tolerating |
| `RateLimitingSustained` | `> 0` for 10m | unchanged | still a starting point — nothing here measured caller behaviour |
| `StreamAbsoluteMaxHit` | `> 0` | unchanged | fired zero times across the streaming runs, which is the intended reading: non-zero means the inactivity bounds broke |

The `CircuitOpen` one is the one that mattered. A `for` longer than the state's
own lifetime is a silent no-op: the rule evaluates, never triggers, and looks
healthy on a dashboard. It would have missed an upstream that died for half a
minute and recovered — the exact event the gauge exists to report.

Validated with `promtool check rules` (6 rules, PromQL parsed).

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

**5. A deterministic mock cannot measure retry.** The natural resilience arm —
`MOCK_ERROR_RATE=0.2`, direct vs gateway — reported the gateway as *worse* than
direct (21.75% vs 20.98% client errors) while doing 783 extra upstream calls. The
mock hashes the request body into its failure verdict and a retry replays that
body unchanged, so every attempt draws the same verdict. Nothing was wrong with
the gateway; the harness could not express a transient failure. The wall-clock
outage window can, and the same gateway then rescued a third of the failures.
Attempts-per-trace is the tell: above 1.0 with an unmoved error rate means the
arm is measuring itself.

## Not measured

- **Caller behaviour**, which is what `RateLimitingSustained`'s 10-minute
  window assumes; nothing here measured how a real client retries.
- **Capacity at the 256 default.** One `e2-standard-8` driver cannot offer the
  load; it needs several drivers or a slower upstream.
- **A defensible upstream p95.** n=56 supports the p50 used above but not a p95;
  the quantile rule wants 200+ samples, and dynamic shared quota refused 6.7% of
  them, so the surviving latencies are a filtered set.
- **Whether `rpm: 200` is the right route budget.** `config.yaml` calls it a
  self-imposed ceiling pending a measurement of the throughput this project
  needs; the benchmark measured the gateway's capacity, not the app's demand.
- **nginx under a load that stresses it.** Scenario F's numbers all come from a
  gateway bound by `MAX_IN_FLIGHT`, so nginx never worked hard; `keepalive` would
  have to be re-read against a fast upstream, where a handshake is a visible
  fraction of the request.
- **Cross-replica breaker propagation** under load — covered by tests, not by a
  benchmark.
