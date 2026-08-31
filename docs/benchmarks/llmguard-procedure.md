# Benchmark procedure

Commands that produced [llmguard-results.md](llmguard-results.md), in order. Run from
`backend/llmguard/` unless noted. Numbers land in `bench/results/<utc>/`.

On Git Bash: `export MSYS_NO_PATHCONV=1` before any `docker run` with `-v`, or
path conversion turns mounts into junk `;C` directories. Unset it before calling
`gcloud`, which is a Python wrapper and breaks the other way.

Order is a data dependency, not a preference: `MOCK_LATENCY` for A comes from a
real model's latency, and B's "safely below the knee" needs A's knee.

## 0. Sanity — 5 min

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

## 1. Set the upstream's latency

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

## 2. A — capacity ladder

Ceiling is `2 replicas x 256 / 2s = 256 QPS`, so the ladder brackets it and
overshoots one rung.

```bash
QPS_STEPS="20 40 80 160 320 640" DURATION=60s bench/sweep.sh
```

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

## 3. B — overhead

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

## 4. D — circuit breaker

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

## 4b. Cross-replica breaker flag

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

## 5. Streaming

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

## 6. Traces

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

## 7. Reset

Re-comment `MOCK_LATENCY` and `RATE_LIMIT_RPM` in `.env`, then:

```bash
docker compose -f ../../docker-compose.yml --profile multi-replica \
  up -d --force-recreate la-mockupstream la-llmguard-replica
```

Leaving them set makes the stack slow on purpose with no rate limit, which
quietly invalidates whatever is measured next.
