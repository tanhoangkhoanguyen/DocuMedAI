#!/usr/bin/env bash
# Real-provider smoke test: does each adapter translate against a LIVE upstream?
#
# Phase 6 measures the gateway against mockupstream, where the same author wrote
# the attacker and the defender. This asks what that cannot: do vertex.go and
# openai.go produce OpenAI-shaped replies from a real vendor.
#
# Both arms serve the SAME model, so a shape difference between them is an
# adapter bug rather than a vendor difference.
#
# Manual and key-gated, never in CI. Uncomment the `gemini` provider and its
# model_list entry in config.yaml first, then:
#
#   export GOOGLE_CLOUD_PROJECT=... GEMINI_API_KEY=...
#   docker compose up -d la-llmguard
#   bench/smoke-providers.sh
#
# With the observability profile up and OTEL_EXPORTER_OTLP_ENDPOINT set, it also
# prints the per-attempt spans for the requests it just made.
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8081}"
MODEL="${MODEL:-gemini-2.5-flash}"
PROVIDERS="${PROVIDERS:-vertex gemini}"
CLICKHOUSE="${CLICKHOUSE:-la-clickhouse-service}"

pass=0 fail=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }
note() { printf '       %s\n' "$1"; }

need() { command -v "$1" >/dev/null || { echo "missing: $1" >&2; exit 1; }; }
need curl
need python3

# A traceparent per request, so spans are findable by id rather than by guessing
# a time window. otelhttp adopts an inbound one.
hex() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# Checks the fields an OpenAI client actually reads. id and created are asserted
# because the Vertex adapter SYNTHESIZES both -- generateContent returns
# neither -- so an empty id means that synthesis regressed.
CHECK_BUFFERED=$(cat <<'PY'
import json, sys
tid, raw = sys.argv[1], sys.argv[2]
try:
    d = json.loads(raw)
except Exception:
    print("NOT JSON: " + raw[:200]); sys.exit(1)
if "error" in d:
    print("UPSTREAM ERROR: " + json.dumps(d["error"])[:200]); sys.exit(1)
missing = [f for f in ("id", "object", "created", "model", "choices") if f not in d]
if missing:
    print("MISSING FIELDS: %s" % missing); sys.exit(1)
if not d["id"]:
    print("EMPTY id -- adapter did not synthesize one"); sys.exit(1)
if not d["created"]:
    print("ZERO created -- adapter did not synthesize one"); sys.exit(1)
choice = d["choices"][0]
content = (choice.get("message") or {}).get("content") or ""
if not content.strip():
    print("NO content: " + json.dumps(choice)[:200]); sys.exit(1)
if choice.get("finish_reason") != "stop":
    print("finish_reason=%r (want 'stop')" % choice.get("finish_reason")); sys.exit(1)
usage = d.get("usage") or {}
if not usage.get("total_tokens"):
    print("NO usage: " + json.dumps(usage)[:120]); sys.exit(1)
print("trace=%s content=%r tokens=%s/%s model=%s" % (
    tid, content.strip()[:40], usage.get("prompt_tokens"),
    usage.get("completion_tokens"), d["model"]))
PY
)

# A failed stream still ends with `data: [DONE]` -- proxy.go emits it regardless
# so a client's read loop terminates -- and the failure arrives as an in-band
# frame carrying "error". Terminating cleanly therefore proves nothing; the
# frames themselves have to be read.
CHECK_STREAM=$(cat <<'PY'
import json, sys
tid, blob = sys.argv[1], sys.argv[2]
data = [l[5:].strip() for l in blob.splitlines() if l.startswith("data:")]
if not data:
    print("NO SSE FRAMES: " + blob[:200].replace("\n", " ")); sys.exit(1)
if data[-1] != "[DONE]":
    print("NO [DONE] terminator, last=%r" % data[-1][:120]); sys.exit(1)
frames, text, finish, unparsed = [], [], None, False
for payload in data[:-1]:
    try:
        f = json.loads(payload)
    except Exception:
        # SSE lets one event span several data: lines; the proxy scans line by
        # line and would mis-split such a frame.
        unparsed = True
        continue
    if "error" in f:
        print("IN-BAND ERROR: " + json.dumps(f["error"])[:200]); sys.exit(1)
    if f.get("object") != "chat.completion.chunk":
        print("BAD object=%r" % f.get("object")); sys.exit(1)
    frames.append(f)
    for ch in f.get("choices") or []:
        text.append((ch.get("delta") or {}).get("content") or "")
        finish = ch.get("finish_reason") or finish
if unparsed:
    print("UNPARSEABLE FRAME -- upstream may split one event over several data: lines")
    sys.exit(1)
if not frames:
    print("NO CHUNKS before [DONE]"); sys.exit(1)
if not "".join(text).strip():
    print("EMPTY delta text across %d frames" % len(frames)); sys.exit(1)
if finish != "stop":
    print("finish_reason=%r (want 'stop')" % finish); sys.exit(1)
# Known asymmetry, not a failure: the buffered path mints id/created, streaming
# ships "" and 0. Reported so a change either way is visible.
ids = set(f.get("id", "") for f in frames)
print("trace=%s frames=%d chars=%d finish=%s id=%s" % (
    tid, len(frames), len("".join(text)), finish,
    "empty" if ids == set([""]) else sorted(ids)[:1]))
PY
)

buffered() {
  local prov="$1" tid body
  tid="$(hex 16)"
  body=$(curl -sS --max-time 120 "$BASE_URL/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -H "traceparent: 00-${tid}-$(hex 8)-01" \
    -d "{\"provider\":\"$prov\",\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: OK\"}]}")
  python3 -c "$CHECK_BUFFERED" "$tid" "$body"
}

streaming() {
  local prov="$1" tid raw
  tid="$(hex 16)"
  raw=$(curl -sS -N --max-time 120 "$BASE_URL/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -H "traceparent: 00-${tid}-$(hex 8)-01" \
    -d "{\"provider\":\"$prov\",\"model\":\"$MODEL\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"Count from 1 to 10, one number per line.\"}]}")
  python3 -c "$CHECK_STREAM" "$tid" "$raw"
}

echo "LLMGuard real-provider smoke test -> $BASE_URL"
if ! curl -sS --max-time 5 "$BASE_URL/healthz" >/dev/null 2>&1; then
  echo "gateway not reachable at $BASE_URL" >&2
  exit 1
fi

for prov in $PROVIDERS; do
  echo
  echo "$prov / $MODEL"
  for kind in buffered streaming; do
    if out=$("$kind" "$prov"); then ok "$kind"; else bad "$kind"; fi
    note "$out"
  done
done

# The AC asks for per-attempt durations queryable by TraceId. Skipped rather
# than failed when tracing is off, since off is the default.
echo
if docker exec "$CLICKHOUSE" clickhouse-client -d otel -q 'SELECT 1' >/dev/null 2>&1; then
  echo "spans (last 5 min)"
  docker exec "$CLICKHOUSE" clickhouse-client -d otel -q "
    SELECT SpanName, count() AS spans, uniqExact(TraceId) AS traces,
           round(avg(Duration)/1e9, 3) AS avg_s
    FROM otel_traces WHERE Timestamp > now() - INTERVAL 5 MINUTE
    GROUP BY SpanName ORDER BY spans DESC FORMAT PrettyCompact" 2>&1 | sed 's/^/  /'
else
  echo "spans: skipped -- $CLICKHOUSE unreachable (needs the observability"
  echo "       profile and OTEL_EXPORTER_OTLP_ENDPOINT set)"
fi

echo
echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
