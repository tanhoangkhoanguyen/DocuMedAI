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

export const options = {
  scenarios: {
    steady: {
      executor: "constant-arrival-rate",
      rate: QPS,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: Math.min(MAX_VUS, Math.max(10, QPS * 2)),
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

export default function () {
  const res = http.post(`${BASE_URL}/v1/chat/completions`, payload(__ITER), {
    headers: { "Content-Type": "application/json" },
    // Long enough not to cut a slow-but-healthy reply, since a client timeout
    // would be recorded as a gateway failure.
    timeout: "120s",
  });

  shed.add(res.status === 429);
  upstreamErr.add(res.status >= 500);
  if (res.status === 200) {
    served.add(res.timings.duration);
  }

  check(res, {
    "not a gateway error": (r) => r.status < 500,
    "not malformed": (r) => r.status !== 400,
  });
}

// Records the config alongside the numbers, so a result file says what produced it
// — the same discipline the vector-DB lab uses. Only written when asked for, since
// returning anything from handleSummary REPLACES k6's own end-of-test report.
export function handleSummary(data) {
  if (__ENV.SUMMARY_JSON !== "1") {
    return {}; // keep k6's default text summary on stdout
  }
  const out = {
    config: { BASE_URL, PROVIDER, MODEL, QPS, DURATION, STREAM, MAX_VUS },
    metrics: data.metrics,
  };
  return { "bench-summary.json": JSON.stringify(out, null, 2) };
}
