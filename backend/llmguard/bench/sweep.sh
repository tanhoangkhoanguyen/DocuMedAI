#!/bin/bash
# QPS ladder (scenario A / C2): one k6 run per step, watch-metrics.sh scraping
# alongside. Each step lands in bench/results/<utc>/qps-<n>/ with the config
# that produced it.
#
# Usage, from backend/llmguard/:
#   bench/sweep.sh                                          # A: 10 -> 320
#   QPS_STEPS="1 2 3 5 8 13" STOP_ON_429=1 bench/sweep.sh   # C2: stop at 1st 429
#
# load.js knobs pass through (BASE_URL, PROVIDER, MODEL, MODE, STREAM,
# VERTEX_URL, VERTEX_TOKEN); NETWORK overrides the compose network name.
#
# Scenario A prerequisite, NOT done here: raise RATE_LIMIT_RPM in .env and
# restart the profile, or the ladder measures the limiter instead of admission.
set -euo pipefail

QPS_STEPS="${QPS_STEPS:-10 20 40 80 160 320}"
DURATION="${DURATION:-60s}"
NETWORK="${NETWORK:-documedai_documedai-net}"
STOP_ON_429="${STOP_ON_429:-0}"
RUN_DIR="bench/results/$(date -u +%Y%m%dT%H%M%SZ)"

for qps in $QPS_STEPS; do
  step="$RUN_DIR/qps-$qps"
  mkdir -p "$step"
  echo "== QPS $qps -> $step"

  watcher=$(docker run -d --rm --network "$NETWORK"     -v "$PWD/bench/watch-metrics.sh:/watch.sh:ro" -v "$PWD/$step:/out"     alpine:3 sh /watch.sh)

  # A failed step = the driver could not offer this QPS; higher steps would lie.
  docker run --rm -i --network "$NETWORK"     -e BASE_URL -e PROVIDER -e MODEL -e MODE -e STREAM -e VERTEX_URL -e VERTEX_TOKEN     -e QPS="$qps" -e DURATION="$DURATION" -e SUMMARY_JSON=1     -v "$PWD/$step:/out" -w /out     grafana/k6:latest run - < bench/load.js || {
    docker stop "$watcher" > /dev/null 2>&1 || true
    echo "!! driver fell behind at QPS $qps -- ladder stops here"
    exit 1
  }

  docker stop "$watcher" > /dev/null 2>&1 || true

  # "passes" on a Rate metric counts true adds -- here, responses that were 429.
  if [ "$STOP_ON_429" = "1" ] &&
    ! grep -A8 '"shed_rate"' "$step/bench-summary.json" | grep -q '"passes": 0[,}]'; then
    echo "== first 429s at QPS $qps -- stopping as asked"
    break
  fi
done

echo "== done: $RUN_DIR"
