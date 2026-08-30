// Open-loop load driver for LLMGuard.
//
// The executor is `constant-arrival-rate`: it starts an iteration on a SCHEDULE
// rather than when the previous one finishes. That distinction is the whole point.
// A closed-loop driver (a loop of curl, or k6's ramping-vus) sends the next request
// only after the last reply arrives, so when the gateway slows down the driver
// slows with it — offered load silently drops and the tail latency that matters
// never appears. This is coordinated omission, and it makes an overloaded system
// look healthy.
//
// k6 reports it: `dropped_iterations` counts iterations it could not start on time,
// which is the driver admitting it fell behind. A run with dropped_iterations > 0
// did not offer the QPS it claims, and its latency numbers describe a lower rate.
//
// Usage (from backend/llmguard/, with the multi-replica profile up):
//
//   docker run --rm -i --network documedai-net \
//     -e BASE_URL=http://la-nginx:80 -e PROVIDER=mock -e QPS=20 -e DURATION=30s \
//     grafana/k6:latest run - < bench/load.js
//
// Results land as JSON on stdout when SUMMARY_JSON=1, so a runner can archive the
// config that produced them alongside the numbers.
import http from "k6/http";
import { check } from "k6";
import { Trend, Rate } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://la-nginx:80";
const PROVIDER = __ENV.PROVIDER || "mock";
const MODEL = __ENV.MODEL || "gemini-2.5-flash";
// vertex-direct = the no-proxy arm, Gemini native shape. A direct-to-MOCK arm
// needs no mode: point BASE_URL at la-mockupstream:8090.
const MODE = __ENV.MODE || "gateway";
// Full :generateContent URL + OAuth token (gcloud auth print-access-token, ~1h).
const VERTEX_URL = __ENV.VERTEX_URL || "";
const VERTEX_TOKEN = __ENV.VERTEX_TOKEN || "";
const QPS = Number(__ENV.QPS || 20);
const DURATION = __ENV.DURATION || "30s";
const STREAM = (__ENV.STREAM || "false") === "true";
// Headroom, not a latency estimate: too low shows up as dropped_iterations
// and fails the run; too high costs only idle-VU memory.
const MAX_VUS = Number(__ENV.MAX_VUS || Math.max(50, QPS * 10));

// Separate from k6's own http_req_duration so the two can be compared: this one is
// recorded only for requests the gateway actually served, while http_req_duration
// includes the 429s a shedding gateway returns in microseconds — which would drag
// the percentiles DOWN exactly when the system is failing.
const served = new Trend("served_duration", true);
const shed = new Rate("shed_rate");
const upstreamErr = new Rate("upstream_error_rate");
// TTFT through the proxy chain: first byte of a streamed reply. Buffered
// replies arrive whole, so recorded only when STREAM=true.
const ttft = new Trend("ttft", true);

export const options = {
  scenarios: {
    steady: {
      executor: "constant-arrival-rate",
      rate: QPS,
      timeUnit: "1s",
      duration: DURATION,
      // All of it up front: spawning VUs mid-run is itself what drops iterations.
      preAllocatedVUs: MAX_VUS,
      maxVUs: MAX_VUS,
    },
  },
  // Only the driver is asserted on: drops mean the QPS label is a lie.
  // Shedding is a result to report, not a failure.
  thresholds: {
    dropped_iterations: ["count==0"],
  },
  summaryTrendStats: ["min", "med", "p(95)", "p(99)", "max", "avg"],
};

const payload = (i) =>
  JSON.stringify({
    provider: PROVIDER,
    model: MODEL,
    stream: STREAM,
    // Distinct content per iteration: mockupstream derives both its reply and its
    // chaos verdict from the request bytes, so identical bodies would share one
    // verdict and collapse the run into a single sample repeated N times.
    messages: [{ role: "user", content: `load ${i} ${__VU}` }],
  });

const vertexPayload = (i) =>
  JSON.stringify({
    contents: [{ role: "user", parts: [{ text: `load ${i} ${__VU}` }] }],
  });

export default function () {
  const res =
    MODE === "vertex-direct"
      ? http.post(VERTEX_URL, vertexPayload(__ITER), {
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${VERTEX_TOKEN}`,
          },
          timeout: "120s",
        })
      : http.post(`${BASE_URL}/v1/chat/completions`, payload(__ITER), {
          headers: { "Content-Type": "application/json" },
          // Long enough not to cut a slow-but-healthy reply, since a client timeout
          // would be recorded as a gateway failure.
          timeout: "120s",
        });

  // In vertex-direct mode a 429 is Google's, not a shed; config.MODE in the
  // summary keeps them apart.
  shed.add(res.status === 429);
  // A status code alone cannot say WHOSE 429 this is: the gateway shedding, its
  // rate limiter, or the upstream's own quota. Off by default because one line
  // per failure buries the summary at any real error rate.
  if (__ENV.DEBUG_ERRORS === "1" && res.status !== 200) {
    console.log(`status=${res.status} ${String(res.body).slice(0, 300)}`);
  }
  upstreamErr.add(res.status >= 500);
  if (res.status === 200) {
    served.add(res.timings.duration);
    if (STREAM) {
      ttft.add(res.timings.waiting);
    }
  }

  check(res, {
    "not a gateway error": (r) => r.status < 500,
    "not malformed": (r) => r.status !== 400,
  });
}

// Exporting handleSummary REPLACES k6's own report (returning {} silences it),
// so stdout is always written by hand here. SUMMARY_JSON=1 additionally archives
// the full metrics with the config that produced them — the vector-DB lab's
// discipline.
export function handleSummary(data) {
  const v = (n) => (data.metrics[n] && data.metrics[n].values) || {};
  const ms = (x) => (x === undefined ? "-" : x.toFixed(1) + "ms");
  const lines = [
    `iterations=${v("iterations").count || 0} dropped=${v("dropped_iterations").count || 0}`,
    `shed_rate=${(v("shed_rate").rate || 0).toFixed(4)} upstream_error_rate=${(v("upstream_error_rate").rate || 0).toFixed(4)}`,
  ];
  for (const n of ["served_duration", "ttft", "http_req_duration"]) {
    const t = v(n);
    if (t.med !== undefined)
      lines.push(`${n}: med=${ms(t.med)} p95=${ms(t["p(95)"])} p99=${ms(t["p(99)"])} max=${ms(t.max)}`);
  }
  const out = { stdout: "\n" + lines.join("\n") + "\n" };
  if (__ENV.SUMMARY_JSON === "1") {
    out["bench-summary.json"] = JSON.stringify(
      {
        config: { BASE_URL, PROVIDER, MODEL, MODE, QPS, DURATION, STREAM, MAX_VUS },
        metrics: data.metrics,
      },
      null,
      2,
    );
  }
  return out;
}
