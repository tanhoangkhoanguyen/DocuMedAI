#!/bin/sh
# Scrapes llmguard_in_flight + refusal counters from every replica, 1/s, to CSV
# -- the run's only view of occupancy vs MAX_IN_FLIGHT. Scrapes each replica
# directly (resolved via compose DNS): /metrics through nginx round-robins
# across per-replica registries.
#
# Run inside the compose network; stop with docker stop. sweep.sh drives it.
HOST="${HOST:-la-llmguard-replica}"
PORT="${PORT:-8081}"
INTERVAL="${INTERVAL:-1}"
OUT="${OUT:-/out/metrics.csv}"

echo "ts,replica,in_flight,shed_total,rate_limited_total" > "$OUT"

# Sum of one metric's samples (counters carry labels; the gauge passes through).
# Matched by string, not regex: a "^name($|{)" pattern has to survive both shell
# and awk quoting, and silently matches nothing when it does not -- which reads
# as a flat zero gauge rather than as an error.
scrape() { # $1=ip $2=metric
  wget -qO- -T 2 "http://$1:$PORT/metrics" 2>/dev/null |
    awk -v m="$2" '$1 == m || index($1, m "{") == 1 { s += $NF } END { printf "%s", s+0 }'
}

while :; do
  ts=$(date +%s)
  # busybox nslookup lists the DNS server first; keep only answer-section IPs.
  for ip in $(nslookup "$HOST" 2>/dev/null | awk '/^Address/ && $2 !~ /:/ && $2 != "127.0.0.11" { print $2 }'); do
    echo "$ts,$ip,$(scrape "$ip" llmguard_in_flight),$(scrape "$ip" llmguard_shed_total),$(scrape "$ip" llmguard_rate_limited_total)" >> "$OUT"
  done
  sleep "$INTERVAL"
done
