# Benchmark procedure

Commands that produced [llmguard-results.md](llmguard-results.md), in order. Run from
`backend/llmguard/` unless noted. Numbers land in `bench/results/<utc>/`.

**One machine or two.** The commands below assume both driver and stack on one
host, joined by `--network documedai_documedai-net`. The published run used two
GCP VMs ([llmguard-gcp.md](llmguard-gcp.md)), where three things change: the
driver reaches the stack over `http://$STACK:8082` and joins no Docker network,
`sudo` prefixes every stack-side `docker` (the Console SSH user is not in the
`docker` group), and `sweep.sh` only runs stack-side. Each section says which
host it belongs to when it is not obvious.

On Git Bash: `export MSYS_NO_PATHCONV=1` before any `docker run` with `-v`, or
path conversion turns mounts into junk `;C` directories. Unset it before calling
`gcloud`, which is a Python wrapper and breaks the other way.

Order is a data dependency, not a preference: `MOCK_LATENCY` for A comes from a
real model's latency, and B's "safely below the knee" needs A's knee.

## 0. Sanity — three things every number assumes

Never skip. Every later number assumes these three.

```bash
docker compose -f ../../docker-compose.yml --profile multi-replica up -d
```

**Driver offers the QPS it claims.** Want `dropped=0`:

```bash
docker run --rm -i --network documedai_documedai-net \
  -e BASE_URL=http://la-nginx:80 -e PROVIDER=mock -e QPS=20 -e DURATION=20s \
  grafana/k6:latest run - < bench/load.js
```

**Both replicas get traffic.** Want two numbers of the same order — a >2x split
means `least_conn` is not doing what it looks like. Replicas are distroless, so
scrape from a container that has `wget`:

```bash
docker run --rm --network documedai_documedai-net alpine:3 sh -c '
for ip in $(nslookup la-llmguard-replica 2>/dev/null | awk "/^Address/ && \$2 !~ /:/ && \$2 != \"127.0.0.11\" {print \$2}"); do
  echo -n "$ip: "; wget -qO- -T 2 http://$ip:8081/metrics | awk "/^llmguard_requests_total/{s+=\$NF} END{print s+0}"
done'
```

**Collector sees both.** Run any step below and confirm `metrics.csv` has two
distinct replica IPs and a non-zero `in_flight`. A flat zero column is the bug
`aa8e86d` fixed, and it looks like an idle gateway rather than an error.

**Restart nginx after any `--force-recreate` of the replicas.** nginx resolves
the upstream name once at startup — there is no `resolver` directive — so
replicas that came back on new IPs are invisible to it, and traffic piles onto
whichever old IP still answers. This reads exactly like a load-balancing bug and
is not one: after `up -d --force-recreate la-nginx`, 40 requests split 20/20.

**Set benchmark vars in `.env`, not inline.** `la-llmguard-replica` and
`la-mockupstream` load `.env` via `env_file`, which overrides `VAR=x docker
compose ...` on the command line. Verify with `docker inspect` rather than
assuming the recreate took.

## 1. Set the upstream's latency and lift the rate limit

The mock answers in ~1ms natively; at that speed `MAX_IN_FLIGHT=256` is
unreachable at any QPS a driver can offer. 2000ms stands in for a real model.
Uncomment in `.env` (project root):

```
MOCK_LATENCY=2000ms
RATE_LIMIT_RPM=1000000
```

`RATE_LIMIT_RPM` is raised so scenario A measures admission control rather than
the token bucket — the default 480 RPM is 8 QPS and would refuse everything past
the first rung.

```bash
docker compose -f ../../docker-compose.yml --profile multi-replica \
  up -d --force-recreate la-mockupstream la-llmguard-replica
```

**Verify it applied** — a restart is not enough, compose only forwards vars the
service declares:

```bash
docker inspect la-mockupstream-service --format '{{range .Config.Env}}{{println .}}{{end}}' | grep MOCK_
```

Then confirm a round trip takes ~2s. Ignore the first `curl` from Windows; cold
start there costs seconds and is not the gateway.

## 2. Capacity ladder — where shedding starts (A)

Ceiling is `2 replicas x MAX_IN_FLIGHT / 2s`. The ladder must bracket it and
overshoot one rung, or shedding never starts and there is nothing to read.

```bash
QPS_STEPS="20 40 80 160 320 640" DURATION=60s bench/sweep.sh
```

**Two VMs (the published run).** `sweep.sh` joins the compose network by name, so
it only runs on the stack machine. From the driver, loop k6 directly:

```bash
export STACK=<stack internal IP>
for q in 40 80 160 320; do
  echo "===== QPS $q"
  docker run --rm -i -e BASE_URL=http://$STACK:8082 -e PROVIDER=mock     -e QPS=$q -e DURATION=45s     grafana/k6:latest run - < bench/load.js 2>&1 | grep -E 'iterations=|shed_rate|served_duration'
done
```

At the 256 default one `e2-standard-8` cannot reach the knee — it drops
iterations before the gateway sheds, and a rung the driver lost measures the
driver. Lower the ceiling instead of raising load: set `MAX_IN_FLIGHT=64` in the
stack's `.env`, recreate the replicas, and confirm with `docker inspect` before
believing any rung.

Occupancy is scraped from a replica directly, not through nginx (each replica has
its own registry, so `/metrics` through the load balancer round-robins). Replicas
are distroless — use a sidecar, and start it just before the k6 run:

```bash
sudo docker run --rm --network documedai_documedai-net alpine:3 sh -c '
for i in $(seq 1 40); do
  wget -qO- -T 2 http://<replica ip>:8081/metrics | awk "/^llmguard_in_flight /{print \$2}"
  sleep 0.5
done'
```

Occupancy pinned at the ceiling is what separates "admission control is working"
from "the load never arrived" — both look like a flat goodput curve otherwise.

Read each step:

```bash
RUN=$(ls -d bench/results/*/ | tail -1)
for d in $RUN/qps-*; do
  echo "== $(basename $d)"
  grep -A6 '"shed_rate"' $d/bench-summary.json | grep '"rate"'
  awk -F, 'NR>1 && $3>m[$2]{m[$2]=$3} END{for(r in m) print "  in_flight", r, m[r]}' $d/metrics.csv
done
```

Goodput is `QPS x (1 - shed_rate)`. After shedding starts it should stay flat: a
plateau means admission control is refusing cheaply, a decline means the gateway
is spending itself on refusals.

**Read a failed rung by its shape first.** 429s are a result. Connection resets
and nginx 499s are the driver running out of room, and that rung says nothing
about the gateway:

```bash
docker logs la-nginx-service 2>&1 | tail -20
```

## 3. Gateway overhead vs direct (B)

20 QPS is far below A's knee, so nothing queues; 60s gives 1200 samples, enough
for p99. Same body, same upstream, one hop apart.

```bash
# direct
docker run --rm -i --network documedai_documedai-net \
  -e BASE_URL=http://la-mockupstream:8090 -e QPS=20 -e DURATION=60s \
  grafana/k6:latest run - < bench/load.js

# through the gateway
docker run --rm -i --network documedai_documedai-net \
  -e BASE_URL=http://la-nginx:80 -e PROVIDER=mock -e QPS=20 -e DURATION=60s \
  grafana/k6:latest run - < bench/load.js
```

The difference in `served_duration` is the overhead. Report it against the
upstream's own latency, or the millisecond means nothing on its own.

## 3b. Vertex as a baseline — why it cannot be one (C)

Kept because the negative result is the finding: pay-as-you-go Vertex has no
fixed QPS to measure against. Needs a real GCP project with ADC, so it runs
against `la-llmguard` (the `config.yaml` stack) rather than the mock replicas.

```bash
# a token lives ~1h; MSYS_NO_PATHCONV breaks gcloud, unset it first
export VERTEX_TOKEN=$(gcloud auth print-access-token)
export VERTEX_URL="https://$GOOGLE_CLOUD_LOCATION-aiplatform.googleapis.com/v1/projects/$GOOGLE_CLOUD_PROJECT/locations/$GOOGLE_CLOUD_LOCATION/publishers/google/models/gemini-2.5-flash:generateContent"

# direct arm, no gateway
docker run --rm -i --network documedai_documedai-net \
  -e MODE=vertex-direct -e VERTEX_URL="$VERTEX_URL" -e VERTEX_TOKEN="$VERTEX_TOKEN" \
  -e QPS=1 -e DURATION=200s grafana/k6:latest run - < bench/load.js
```

Read `shed_rate` as **Google's** 429s here, not the gateway's — `MODE` in the
summary is what keeps the two apart. Two runs minutes apart returned wildly
different refusal rates at the same QPS, which is the result: dynamic shared
quota means each number describes Google's global load at that minute.

Run it twice before concluding anything. One run showing a clean 1 QPS proves
nothing about the next.

## 4. Circuit breaker trip threshold (D)

Both sides of `CircuitFailRatio=0.6`. Restart the replicas between rows: the
breaker's window is 60s, and a run that just finished still counts.

```bash
# under the threshold -- must NOT open
MOCK_ERROR_RATE=0.3 docker compose -f ../../docker-compose.yml --profile multi-replica \
  up -d --force-recreate la-mockupstream la-llmguard-replica
```

```bash
docker run --rm -i --network documedai_documedai-net \
  -e BASE_URL=http://la-nginx:80 -e PROVIDER=mock -e QPS=20 -e DURATION=60s \
  grafana/k6:latest run - < bench/load.js

docker run --rm --network documedai_documedai-net alpine:3 sh -c '
for ip in $(nslookup la-llmguard-replica 2>/dev/null | awk "/^Address/ && \$2 !~ /:/ && \$2 != \"127.0.0.11\" {print \$2}"); do
  v=$(wget -qO- -T 2 http://$ip:8081/metrics | awk "/^llmguard_circuit_state/{print \$NF}")
  echo "$ip circuit=${v:-none}"
done'
```

`circuit=none` is correct here: the gauge is only written by `OnStateChange`, so
a breaker that never left closed has no series at all.

Repeat with `MOCK_ERROR_RATE=0.8` and expect `circuit=2`. To prove the `Interval`
fix rather than just the breaker, send healthy traffic first (30s at 40 QPS with
`MOCK_ERROR_RATE` unset), then switch to 0.8 — that healthy history is what used
to make the breaker un-trippable.

`DEBUG_ERRORS=1` prints response bodies, which is the only way to tell whose 429
or 503 you are looking at.

## 4b. Cross-replica breaker flag — a peer's outage (D)

Drive ONE replica by its IP (not through nginx) so the other stays untouched,
then send the untouched one a handful of requests. It refuses them without
calling upstream, and its own `circuit_state` never appears — proof the refusal
came from the flag rather than from its own breaker.

```bash
# reset, then read the two replica IPs
docker exec la-redis-service redis-cli -n 1 DEL llmguard:breaker:open:mock:gemini-2.5-flash
docker compose -f ../../docker-compose.yml --profile multi-replica \
  up -d --force-recreate la-llmguard-replica && sleep 15
docker run --rm --network documedai_documedai-net alpine:3 sh -c \
  'nslookup la-llmguard-replica 2>/dev/null | awk "/^Address/ && \$2 !~ /:/ && \$2 != \"127.0.0.11\" {print \$2}"'
```

Load the first IP directly (`-e BASE_URL=http://<loaded-ip>:8081`), then hit the
other with ~12 requests and read its metrics. Repeat after
`redis-cli -n 1 DEL llmguard:breaker:open:...` for the contrast: without the flag
the same replica burns `CIRCUIT_MIN_REQUESTS` requests at ~10.3s each before
tripping itself.

Timing note: the flag's TTL is `CIRCUIT_OPEN_FOR` (20s), so a `TTL` of `-2` a
minute after a run means it expired, not that it was never published. To catch
publication, poll `EXISTS` during the run.

## 5. Streaming — time to first token

k6's `ttfb_header` stops at the response header and cannot see buffering. Use
`curl`, which reports time to the first body byte:

```bash
docker run --rm --network documedai_documedai-net alpine:3 sh -c '
apk add --no-cache curl >/dev/null 2>&1
curl -s -o /dev/null -w "ttfb=%{time_starttransfer}s total=%{time_total}s\n" \
  -X POST http://la-nginx:80/v1/chat/completions -H "Content-Type: application/json" \
  -d "{\"provider\":\"mock\",\"model\":\"gemini-2.5-flash\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"probe\"}]}"'
```

To compare `proxy_buffering` settings, edit `nginx/nginx.conf.template` and
recreate `la-nginx`. Change one variable at a time — the mock's frame count and
chunk delay must stay fixed, or the two runs are not comparable.

Restore the template with `git checkout --` rather than a second `sed`: `sed -i`
rewrites the file to LF and the repo keeps it CRLF.

## 5a. Tracing cost on the request path (E)

Both arms at the same rung below the shed point, so latency is clean. Start with
tracing off (the default: `OTEL_EXPORTER_OTLP_ENDPOINT` unset) and run the driver
line, then turn it on and repeat.

```bash
# stack: bring the collector up and point the replicas at it
sudo docker compose --profile observability up -d la-otel-collector la-clickhouse
echo 'OTEL_EXPORTER_OTLP_ENDPOINT=http://la-otel-collector:4318' >> .env
sudo docker compose --profile multi-replica up -d --force-recreate la-llmguard-replica la-nginx
```

```bash
# driver: identical to the off arm
docker run --rm -i -e BASE_URL=http://$STACK:8082 -e PROVIDER=mock \
  -e QPS=40 -e DURATION=60s grafana/k6:latest run - < bench/load.js
```

**Count the spans before believing the delta.** Export is asynchronous and drops
silently at three points, and a failed exporter produces exactly the "no overhead"
result the arm is looking for:

```bash
sudo docker exec la-clickhouse-service clickhouse-client -d otel \
  -q "SELECT count(), countDistinct(TraceId) FROM otel_traces"
```

`countDistinct(TraceId)` must equal the driver's iteration count. Anything less
means spans were dropped and the comparison is between tracing-off and
tracing-partly-off.

## 5b. nginx calibration — four defaults (F)

Four numbers, four different measurements. Run these on the **stack** VM; only
the k6 line runs on the driver.

**CPU per worker.** Take one sample idle and one ~12s into a 320 QPS run:

```bash
sudo docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}' | head -8
```

Under 50% for `la-nginx-service` means `worker_processes 1` has headroom. Note
what else is idle: a mock that sleeps 2s costs no CPU, so nothing here competes.

**Connection pool and churn.** nginx exports no connection metrics, so count
sockets from inside its network namespace. Start this first, then the k6 run:

```bash
sudo docker run --rm --network container:la-nginx-service nicolaka/netshoot sh -c 'for i in $(seq 1 20); do echo "$i EST=$(ss -tan | grep -c "ESTAB.*:8081") TW=$(ss -tan | grep -c "TIME-WAIT.*:8081")"; sleep 2; done'
```

`EST` is real concurrency (it tracked `MAX_IN_FLIGHT x replicas` exactly). `TW`
is **not** a pool signal — run it once at a QPS below the shed point and once
above: it stays flat when nothing is refused and climbs with the shed rate.
Comparing a `keepalive` change needs both runs at the same QPS.

**Peer split.** Which upstream IPs nginx actually opens connections to:

```bash
sudo docker run --rm --network container:la-nginx-service nicolaka/netshoot sh -c 'sleep 10; ss -tan | grep ESTAB | grep 8081 | awk "{print \$5}" | cut -d: -f1 | sort | uniq -c'
```

**Failover.** Stop one replica and send real requests — use a model the config
allows, or a 400 will look like a working failover without proving anything:

```bash
sudo docker stop documedai-la-llmguard-replica-2 && sleep 2
for i in $(seq 1 10); do
  sudo docker run --rm --network documedai_documedai-net alpine:3 wget -qO- -T 8 \
    --post-data='{"provider":"mock","model":"gemini-2.5-flash","messages":[{"role":"user","content":"x"}]}' \
    --header='Content-Type: application/json' -S http://la-nginx:80/v1/chat/completions 2>&1 |
    grep -E "HTTP/" | head -1
done
sudo docker start documedai-la-llmguard-replica-2
```

200s mean `max_fails` ejects one peer, not the pool. Recreate `la-nginx`
afterwards — it holds the restarted replica's old IP otherwise.

## 6. Retry amplification, from traces (C)

```bash
docker exec la-clickhouse-service clickhouse-client -d otel -q "
SELECT SpanName, count() AS spans, uniqExact(TraceId) AS traces,
       round(count()/uniqExact(TraceId),2) AS per_trace
FROM otel_traces WHERE Timestamp > now() - INTERVAL 20 MINUTE
GROUP BY SpanName ORDER BY spans DESC"
```

`upstream.attempt` per trace is the retry multiplier. Needs
`OTEL_EXPORTER_OTLP_ENDPOINT` set and the `observability` profile up. Spans are
exported asynchronously and dropped silently on overflow, so check
`count(DISTINCT TraceId)` against `llmguard_requests_total` before trusting a
count.

## 7. Reset the stack to its normal config

Re-comment `MOCK_LATENCY` and `RATE_LIMIT_RPM` in `.env`, then:

```bash
docker compose -f ../../docker-compose.yml --profile multi-replica \
  up -d --force-recreate la-mockupstream la-llmguard-replica
```

Leaving them set makes the stack slow on purpose with no rate limit, which
quietly invalidates whatever is measured next.
