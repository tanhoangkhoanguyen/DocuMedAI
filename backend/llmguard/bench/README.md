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

Knobs: `BASE_URL`, `PROVIDER`, `MODEL`, `QPS`, `DURATION`, `STREAM`, `MAX_VUS`,
`SUMMARY_JSON`. The network name carries the Compose project prefix — check
`docker network ls` if it differs.

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

## Not yet done

One arm (gateway -> mock). The direct and LiteLLM arms, the three-arm runner and the
charts are Issue 6.2 / 6.3.
