# LLMGuard ROADMAP

> **Stale in places.** Written before the Vertex AI migration, so Phase 0's code tour still
> describes the OpenAI-compat passthrough: `UPSTREAM_API_KEY`, `OPENAI_UPSTREAM_BASE`,
> `LLM_PROXY_BASE_URL` and `get_llm_base_url()` no longer exist. Those are replaced by
> `GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION` + ADC, and the passthrough is now a
> provider adapter (`provider/`). Phase 2's provider abstraction is **implemented**; its
> first adapter is Vertex. Phase 1 (test scaffold, mock upstream, Go CI) is still open —
> the adapter has unit tests, but nothing runs them automatically.

This is the single roadmap for `llmguard`. It has two parts:

- **Phase 0 — Learn what exists.** The current code was written fast ("vibe-coded"); first
  **understand, verify, and learn** it — a guided tour of the code that already exists, broken into
  small issues. Do this before building anything new.
- **Phases 1–6 — Build what's next.** Turn the current single-upstream reliability gateway into a
  **production-grade, reliability-first LLM gateway** — an OpenAI-compatible, multi-provider proxy
  whose reliability claims are *proven by benchmark*. Implemented **one issue at a time, by hand**.

**North-star story (what the finished project claims)**
> "Reliability-first LLM gateway (Go): OpenAI-compatible multi-provider API with circuit breaking,
> provider-aware retries, and cross-replica dedup. Open-loop, coordinated-omission-aware benchmark
> proves < N ms p99 overhead and 99%+ client success under 20% upstream fault injection (vs ~80%
> direct), validated head-to-head against LiteLLM."

---

# Phase 0 — Learn what exists (do this first)

**Who this is for:** a Go newbie who owns this code and wants to be able to explain every file,
every function, and every design choice — and *prove* to themselves that it does what it claims.

**How each Phase-0 issue is structured**
- **Goal** — what you should understand/verify by the end.
- **What to check** — the exact file, function, and lines to read, plus the concept behind them.
- **Verify (hands-on)** — a concrete thing you *do* (run, curl, print, break-on-purpose) to confirm
  it behaves as described. This is how you learn Go here, not just by reading.
- **Done when** — the checklist that means you truly understand this block.
- (Phases 1–6 issues use **Goal / What to do / Acceptance criteria (AC)** instead.)

**Ground rules**
- Do **not** change behavior in these issues. If you find a real bug, note it in a "Findings" list at
  the bottom of this file; fixing is a separate, later decision.
- Small experiments (adding a temporary `fmt.Println`, a throwaway `_test.go`) are encouraged — just
  revert them after. Keeping a scratch note per issue is the point.
- Order matters: the files build on each other. Follow phases top to bottom.

**The whole system in one sentence:** a request comes in at `/v1/*`, passes through
**rate limit → dedup → circuit breaker → retry**, gets its dummy key swapped for the real upstream
key, is forwarded to an OpenAI-compatible endpoint, and the response (or a stream) is sent back —
with Prometheus metrics recorded throughout.

**Request pipeline (memorize this order — every file maps to a box):**
```
/v1/*  →  Rate limit  →  [stream? pass-through]     ratelimit.go / proxy.go
                      →  Dedup                       dedup.go
                      →  Circuit breaker             retry.go (newBreaker)
                      →  Retry + backoff             retry.go (doWithRetry)
                      →  Build upstream req (swap key) proxy.go (buildUpstreamRequest)
                      →  Forward → response           proxy.go (forwardBuffered)
        (metrics recorded at every step)             metrics.go
```

---

## Phase 0.A — Go & tooling foundations (before reading any proxy logic)

If you can't build and run it, you can't verify anything. Do this first.

### Issue A.1 — Build, run, and hit the service locally
- **Goal:** be able to compile and run the proxy and get a response, so every later "Verify" step works.
- **What to check:**
  - `go.mod` — the 4 dependencies (`prometheus/client_golang`, `redis/go-redis/v9`, `sony/gobreaker`,
    `golang.org/x/sync`) and `go 1.23`. Understand that `go.mod` is Go's dependency manifest.
  - `Dockerfile` — the two stages: builder (`golang:1.23`) → distroless runtime. Note `CGO_ENABLED=0`
    (static binary, so it runs in a shell-less image) and why the healthcheck is a `-healthcheck` flag
    (distroless has no `curl`).
- **Verify (hands-on):**
  - `cd backend/llmguard && go build ./...` — it compiles.
  - `go run . -healthcheck` with nothing running → exits non-zero. Understand why (nothing on :8081).
  - Bring the stack up (`docker compose up -d la-redis la-llmguard`) and `curl http://localhost:8081/healthz`
    → `{"status":"ok"}`.
- **Done when:** you can build locally, and explain what each Dockerfile stage produces and why it's split.

### Issue A.2 — Learn the Go idioms this codebase leans on
- **Goal:** recognize the 5 Go patterns that appear over and over, so the rest reads easily.
- **What to check (find one example of each in the code):**
  1. **Structs + constructors** — `type Proxy struct{...}` + `newProxy(...) *Proxy` (`proxy.go:18,28`).
     Go has no classes; this is the pattern.
  2. **Multiple return values + `error`** — e.g. `take(...) (bool, error)` (`ratelimit.go:100`).
  3. **`context.Context`** — passed as the first arg everywhere (`Acquire(ctx, ...)`); it carries
     cancellation/timeout. See it threaded from `r.Context()` in `proxy.go:78`.
  4. **First-class functions / closures** — `doWithRetry` takes a `call func(ctx) (...)` (`retry.go:78`);
     `deduper.Do` takes an `fn` (`proxy.go:99`). This is how the layers wrap each other.
  5. **`defer`** — `defer resp.Body.Close()` (`proxy.go:144`). Runs on function exit; the Go way to
     guarantee cleanup.
- **Verify (hands-on):** in a scratch `main` or the Go playground, write a 10-line function that
  returns `(int, error)`, call it, and handle the error with `if err != nil`. Write one closure and
  pass it to another function. (Muscle memory — you'll see these shapes constantly.)
- **Done when:** you can point at a line in this repo for each of the 5 patterns and say what it does.

---

## Phase 0.B — Configuration & startup (the skeleton)

### Issue B.1 — `config.go`: how every knob is loaded
- **Goal:** understand that all behavior is env-driven with defaults, and where each default lives.
- **What to check:**
  - `Config` struct (`config.go:15-54`) — read the comment on **every** field. Group them mentally:
    upstream (key/base/port/redis), rate-limit, retry, circuit, http-client.
  - `loadConfig()` (`config.go:57-79`) — note `UPSTREAM_API_KEY` falls back to `OPENAI_API_KEY`
    (`:59`) and `OPENAI_UPSTREAM_BASE` has its trailing `/` trimmed (`:60` — matters for path building
    later in `proxy.go:203`).
  - The 4 `getenv*` helpers (`config.go:82-114`) — the pattern "read env, parse, else default."
- **Verify (hands-on):**
  - Run with `RATE_LIMIT_RPM=60 go run .` and add a temporary `log.Info("cfg", "rpm", cfg.RateLimitRPM)`
    in `main.go` → confirm it prints 60. Remove an env var → confirm the default shows. Revert the log line.
- **Done when:** you can name the default for RPM, burst, retry-max, and circuit-open duration from memory,
  and explain why the base URL's trailing slash is trimmed.

### Issue B.2 — `main.go`: wiring, routes, graceful shutdown, healthcheck
- **Goal:** understand the composition root — how all the pieces are constructed and connected.
- **What to check:**
  - `-healthcheck` branch (`main.go:20-25`) + `runHealthcheck()` (`main.go:92-101`) — the self-probe
    used by Docker.
  - Construction order (`main.go:45-48`): `newMetrics` → `newRateLimiter` → `newDeduper` → `newProxy`.
    Note dependencies are **injected** (passed in), not created inside — this is why it's testable.
  - Routes (`main.go:50-60`): `/v1/` → proxy, `/healthz`, `/metrics`. Understand `http.ServeMux`.
  - Server config (`main.go:62-68`): **no global write timeout** (comment explains: LLM calls are long);
    only `ReadHeaderTimeout`.
  - Graceful shutdown (`main.go:71-88`): the goroutine + signal channel + `srv.Shutdown(ctx)` pattern.
- **Verify (hands-on):**
  - Start the proxy, then `Ctrl-C` (or `docker stop`) and watch the log say `shutting down` — it doesn't
    kill in-flight requests instantly. Explain the 15s timeout (`main.go:84`).
  - `curl http://localhost:8081/metrics` → see Prometheus text. `curl .../healthz` → ok.
- **Done when:** you can draw the startup sequence and explain what would break if `newProxy` were called
  before `newRateLimiter`.

---

## Phase 0.C — The request pipeline (the heart, in pipeline order)

### Issue C.1 — `proxy.go` entry: `ServeHTTP` and why the body is buffered
- **Goal:** understand the single entry point and the first branch (stream vs buffered).
- **What to check:**
  - `Proxy` struct + `newProxy` (`proxy.go:18-46`) — note the shared `http.Client` with a tuned
    `Transport` (connection reuse; read the comment at `:29-36`).
  - `requestMeta` (`proxy.go:50-53`) — only `model` + `stream` are peeked from the body.
  - `ServeHTTP` (`proxy.go:58-93`): body is read **once** into `body` (`:61`) — the comment says why:
    so it can be **replayed on retry and hashed for dedup**. Then rate-limit (`:77`), then the
    stream/buffered fork (`:87`).
- **Verify (hands-on):**
  - `curl -X POST http://localhost:8081/v1/chat/completions -H 'Content-Type: application/json'
    -d '{"model":"gemini-2.5-flash","messages":[{"role":"user","content":"hi"}]}'` and confirm you get a
    response (real key) or an upstream error (no key) — either proves the path executed.
  - Add a temporary `p.log.Info("meta", "model", model, "stream", meta.Stream)` after `:74`, send a
    request, confirm the parsed values. Revert.
- **Done when:** you can explain *why* the body must be read into memory before anything else happens.

### Issue C.2 — `ratelimit.go`: the distributed token bucket (Redis + Lua)
- **Goal:** understand rate limiting and why it's in Redis with an atomic Lua script.
- **What to check:**
  - `RateLimiter` struct + doc comment (`ratelimit.go:19-23`) — why Redis (shared across replicas),
    why keyed per api-key+model.
  - `tokenBucketScript` (`ratelimit.go:34-58`) — read the Lua line by line: **lazy refill**
    (compute tokens accrued since last touch, cap at burst), take one if available, `PEXPIRE` so idle
    buckets self-clean. Understand *why one Lua script* = atomic (no read-modify-write race).
  - `Acquire` (`ratelimit.go:70-98`) — the **poll-until-token-or-deadline** loop, and the crucial
    **fail-open** on Redis error (`:77-80`, returns `true`). Understand the trade-off: availability over
    strict limiting when Redis is down.
  - `take` (`ratelimit.go:100-110`) — builds the key `llmguard:bucket:<key>` and runs the script.
- **Verify (hands-on):**
  - Set `RATE_LIMIT_RPM=6 RATE_LIMIT_BURST=2 RATE_WAIT_MAX=1s` and fire ~10 rapid curls; observe some
    return the 429 body from `proxy.go:80`. Watch `llmguard_rate_limited_total` climb in `/metrics`.
  - In `redis-cli` (DB 1): `HGETALL llmguard:bucket:anon:<model>` right after a burst to see `tokens`/`ts`.
  - **Break-on-purpose:** stop Redis, send a request → it still goes through (fail-open). Explain why
    that's the chosen behavior.
- **Done when:** you can explain lazy refill, why the script is atomic, and what fail-open protects against.

### Issue C.3 — `dedup.go`: in-flight deduplication with singleflight
- **Goal:** understand how identical concurrent requests share ONE upstream call (an important cost/quota feature).
- **What to check:**
  - `Deduper` wraps `singleflight.Group` (`dedup.go:12-16`) — the whole feature is ~20 lines because
    `golang.org/x/sync/singleflight` does the heavy lifting.
  - `dedupKey` (`dedup.go:20-23`) — SHA-256 of the **whole body**; the comment explains the body already
    contains model+messages+params, so it's a complete identity.
  - `Do` (`dedup.go:28-36`) — `group.Do(key, fn)` runs `fn` once per key; concurrent callers with the
    same key **wait and reuse** the result. The `sharedFlight` bool (→ `shared`) tells you a caller
    piggy-backed (drives the `dedupHits` metric in `proxy.go:116-118`).
  - The **extension-point comment** (`dedup.go:38-40`): this is *in-process only* — across replicas it
    doesn't dedup. Understand this limitation clearly (it's a known gap, not a bug).
- **Verify (hands-on):**
  - Write a tiny throwaway `dedup_test.go`: call `d.Do("k", fn)` from ~5 goroutines where `fn` sleeps
    100ms and increments a counter; assert the counter == 1 and that 4 calls report `shared==true`.
    (This is your first real Go test — `go test ./...`.) Delete it after, or keep it as a learning artifact.
  - Send two identical slow requests concurrently and watch `llmguard_dedup_hits_total` go up by 1.
- **Done when:** you can explain what singleflight guarantees, why the SHA-256 of the body is a valid
  identity, and why this dedup does NOT work across multiple replicas.

### Issue C.4 — `retry.go` part 1: the circuit breaker (`newBreaker`)
- **Goal:** understand fail-fast protection when upstream is unhealthy.
- **What to check:**
  - `newBreaker` (`retry.go:43-66`) using `sony/gobreaker`.
  - `ReadyToTrip` (`:47-53`): trips only after `CircuitMinReqs` requests AND failure ratio ≥
    `CircuitFailRatio`. Understand both conditions (don't trip on a tiny sample).
  - `Timeout: cfg.CircuitOpenFor` (`:46`) — how long it stays open before a half-open probe.
  - `OnStateChange` (`:54-64`) — maps closed/half-open/open → the `circuitState` gauge (0/1/2).
  - Note in `proxy.go:102` the breaker wraps the **whole retry loop** — a tripped breaker stops you
    before you even start retrying. Understand that ordering.
- **Verify (hands-on):**
  - Point the proxy at a stub that always 500s (set `OPENAI_UPSTREAM_BASE` to a tiny local server, or
    use an unroutable base). Send > `CIRCUIT_MIN_REQUESTS` requests; watch `llmguard_circuit_state` go
    from 0 → 2, and subsequent requests fail *fast* (503 immediately, no long wait). After
    `CIRCUIT_OPEN_FOR`, watch it probe (1) then recover or re-open.
- **Done when:** you can state the two trip conditions and explain why the breaker wraps retry (not the reverse).

### Issue C.5 — `retry.go` part 2: retry, backoff, jitter, Retry-After
- **Goal:** understand the retry loop and its timing math.
- **What to check:**
  - `isRetryable` (`retry.go:27-37`) — which statuses retry (429/500/502/503/504) and which don't
    (400/401 fail fast). Understand the distinction: transient vs client error.
  - `doWithRetry` (`retry.go:73-115`) — the loop: call → if success/non-retryable return → else record
    `last`, sleep `backoffDelay`, honor `Retry-After` if present (`:100-104`), respect
    `ctx.Done()` (`:106`). Note "don't sleep after the final attempt" (`:95`).
  - `backoffDelay` (`retry.go:120-132`) — `base * 2^attempt`, capped, **plus deterministic jitter**
    from an FNV hash of `seed+attempt`. Understand *why deterministic* (comment `:118-119`: no global
    `math/rand` state; reproducible; concurrent callers still spread out).
  - `parseRetryAfter` (`retry.go:136-144`) — handles the seconds form only.
- **Verify (hands-on):**
  - Write a throwaway `retry_test.go` with a fake `call` that fails N times then succeeds; assert it
    retries the right number of times and returns success. Add one that returns `Retry-After: 1` and
    assert the delay respects it. Add one returning 400 and assert it does NOT retry.
  - Print `backoffDelay(cfg, 0..3, "abc")` for attempts 0–3 and eyeball the exponential growth + jitter.
- **Done when:** you can compute the base delay for attempt 2 by hand, and explain why jitter is derived
  from a hash instead of a random number.

### Issue C.6 — `proxy.go` buffered path: dedup → breaker → retry → forward
- **Goal:** see how C.2–C.5 compose into the real non-streaming request.
- **What to check:**
  - `serveBuffered` (`proxy.go:96-130`) — the nesting: `deduper.Do( breaker.Execute( doWithRetry( forwardBuffered )))`.
    Read it inside-out. Note `shared` → `dedupHits.Inc()` (`:116`), and error → 503 (`:120-126`).
  - `forwardBuffered` (`proxy.go:135-151`) — ONE attempt: build request, `client.Do`, read the full body
    into `upstreamResult{status, header, body}`. Understand why the body is buffered here (so retry can
    discard+replay).
  - `buildUpstreamRequest` (`proxy.go:200-220`) — **the key swap**: strips leading `/v1` (`:203`, pairs
    with the trimmed base from B.1), sets `Authorization: Bearer <real key>` (`:215`), discards the
    caller's dummy auth. This is the security core: the real key lives only in the proxy.
  - `recordUsage` (`proxy.go:224-240`) — best-effort parse of `usage.{prompt,completion}_tokens` → metrics.
  - `finish` (`proxy.go:243-260`) — writes response + records `requests`/`latency` metrics + structured log.
- **Verify (hands-on):**
  - Add temporary logs at the entry of `forwardBuffered` and inside the retry `onRetry` callback; point at
    a flaky stub (fails twice then 200) and watch: one dedup flight, breaker closed, two retries logged,
    one final success — the whole pipeline in one trace.
  - Confirm with a stub that logs its received `Authorization` header that it sees the **real** key, not
    the caller's dummy one. Revert logs.
- **Done when:** you can read `serveBuffered` inside-out and narrate exactly what each wrapping layer does.

### Issue C.7 — `proxy.go` streaming path: `serveStreaming`
- **Goal:** understand why streaming is handled separately and what protections it keeps/loses.
- **What to check:**
  - `serveStreaming` (`proxy.go:155-195`) — requires `http.Flusher` (`:156`), wraps only the **breaker**
    (no retry, no dedup — comment `:153-154` + `:84-86` explain why: a stream can't be buffered/replayed
    as a unit). Reads upstream in 4KB chunks and flushes each (`:178-188`).
  - Note metrics are still recorded (`:176`, `:194`).
  - The current backend never sets `stream=true` (see the note at `proxy.go:86`) — so this path is
    forward-compatible but not exercised in production today.
- **Verify (hands-on):**
  - Send a request with `"stream": true` to a stub that emits a few SSE `data:` lines slowly; confirm you
    receive chunks incrementally (not all at once) — proves per-chunk flushing.
  - Explain what happens if that upstream 500s mid-stream (no retry — the client sees the failure).
- **Done when:** you can articulate the trade-off: streaming gains responsiveness, loses retry/dedup, and why.

### Issue C.8 — small helpers: `apiKeyHint`, `copyHeaders`, `statusLabel`
- **Goal:** finish `proxy.go` — the little functions that keep secrets safe and metrics clean.
- **What to check:**
  - `apiKeyHint` (`proxy.go:266-273`) — last 6 chars of the key as a **non-secret** bucket label
    (never logs the key). Used to build the rate-limit key in `:77`.
  - `copyHeaders` (`proxy.go:275-285`) — skips hop-by-hop headers (`Connection`, `Transfer-Encoding`,
    `Keep-Alive`). Understand why those must not be forwarded verbatim.
  - `statusLabel` (`proxy.go:287-298`) — buckets status into `2xx/4xx/429/5xx` for low-cardinality metrics.
- **Verify (hands-on):** in a throwaway test, assert `apiKeyHint` on a `Bearer sk-...abcdef` returns
  `abcdef` and returns `anon` for short/empty; assert `statusLabel(503)=="5xx"`, `statusLabel(429)=="429"`.
- **Done when:** you can explain why we bucket status codes and why we never log the full key.

---

## Phase 0.D — Observability & the outer edges

### Issue D.1 — `metrics.go`: every metric and what drives it
- **Goal:** map each Prometheus metric back to the code that increments it.
- **What to check:**
  - `Metrics` struct (`metrics.go:11-27`) — read the comment on each field.
  - `newMetrics` (`metrics.go:29-61`) — `promauto` auto-registers; note metric names + labels + the
    latency histogram buckets (`:38`, tuned for slow LLM calls: up to 80s).
  - Cross-reference each metric to its call site: `requests`/`latency` in `finish` (`proxy.go:252-253`)
    and streaming (`:176,194`); `retries` in `serveBuffered` (`:104`); `rateLimited` (`:79`);
    `dedupHits` (`:117`); `circuitState` in `newBreaker` (`retry.go:56-63`); `tokensUsed` in
    `recordUsage` (`:235-238`).
- **Verify (hands-on):** exercise each path (a normal request, a rate-limited burst, a duplicate pair, a
  breaker trip) and confirm the matching metric moves in `/metrics`. This is the best single test that
  you understand the whole system — each metric is a witness to a code path.
- **Done when:** for any metric name in `/metrics`, you can name the exact function+line that changes it.

### Issue D.2 — `README.md` vs reality
- **Goal:** confirm the docs match the code you now understand, and note drift.
- **What to check:** read `README.md` end to end against what you learned. Check the endpoints table,
  the config table (defaults must match `config.go`), the path-stripping note, and the "deferred
  features" list (cross-replica dedup, Grafana, billing) — you've now seen the dedup extension point at
  `dedup.go:38-40`.
- **Verify (hands-on):** for each config default in the README, grep `config.go` and confirm it matches.
- **Done when:** you can list any place the README and code disagree (add to Findings below).

### Issue D.3 — How the Python app actually calls this proxy
- **Goal:** connect the proxy to its one real client, so you see the full loop.
- **What to check:**
  - `backend/services/chatbot/tools/llm_config.py::get_llm_base_url()` — returns
    `http://la-llmguard:8081/v1`, driven by `LLM_PROXY_BASE_URL`.
  - The `ChatOpenAI(..., base_url=get_llm_base_url())` construction sites in
    `backend/services/chatbot/nodes.py` and `tools/rag.py`, and the `crewai.LLM` one in `nodes.py`.
  - `docker-compose.yml` — the `la-llmguard` service (image, port 8081, `UPSTREAM_API_KEY`,
    `OPENAI_UPSTREAM_BASE`, `REDIS_URL` on DB 1, healthcheck) and the `la-documedai` env
    (`LLM_PROXY_BASE_URL`, `depends_on: la-llmguard: service_healthy`).
- **Verify (hands-on):** bring up the full stack, send a chat message through the app UI/API, then check
  `/metrics` on the proxy incremented — proving the app's traffic really flows through your proxy.
- **Done when:** you can trace one chat message from the Python `ChatOpenAI` call → proxy `/v1/chat/completions`
  → upstream and back, naming the env var that wires it.

---

## Findings (fill in as you go)

Log anything that looks wrong, surprising, or worth changing later. Do NOT fix here — just record.
Example rows:
- [ ] `<file:line>` — observed behavior vs expected — severity — idea for fix.

| File:Line | What I observed | Expected? | Note / follow-up |
|-----------|-----------------|-----------|------------------|
|           |                 |           |                  |

---

## End of Phase 0

Once every Phase-0 issue's "Done when" is checked, you can explain the whole proxy to someone else —
that is the gate to building. Phases 1–6 below extend it (multi-provider, OpenTelemetry, ClickHouse,
cross-replica dedup, benchmark). From here, each issue uses **Goal / What to do / AC**.

---

# Phases 1–6 — Build what's next

**How to read these phases**
- Phases are ordered. Do not start a phase before the previous one's acceptance criteria pass.
- Each issue has: **Goal** (why), **What to do** (concrete steps), **Acceptance criteria** (how you
  know it's done). Treat AC as a checklist.

---

## Phase 1 — Test harness & safety net (do this first among the build phases)

**Why first:** every later phase refactors `proxy.go`. Without tests you cannot refactor safely and
cannot *understand* whether a change broke behavior. This phase changes no runtime behavior.

### Issue 1.1 — Add a Go test scaffold
- **Goal:** be able to run `go test ./...` and get meaningful output.
- **What to do:**
  - Add a `Makefile` (or `justfile`) with `test`, `lint`, `run`, `bench` targets.
  - Add `go vet` + `golangci-lint` config (`.golangci.yml`), start permissive.
  - Create `internal/testutil/` for shared test helpers (fake HTTP servers, fixture loaders).
- **AC:**
  - `make test` runs and exits 0 (even with zero tests initially).
  - `make lint` runs clean on the existing code.
  - CI (a new `llmguard-ci.yml` GitHub Action) runs `test` + `lint` on PRs touching `backend/llmguard/**`.

### Issue 1.2 — Build a configurable mock upstream
- **Goal:** a fake LLM server you fully control — the foundation for every resilience test and the
  whole benchmark. **No real API keys, no quota, deterministic.**
- **What to do:**
  - New package `mockupstream/` (a small standalone Go HTTP server, buildable + Dockerable).
  - Serve OpenAI-compat `/v1/chat/completions` (buffered + SSE stream) returning canned responses
    with realistic `usage` fields.
  - Also serve Gemini-native `:generateContent` + `:streamGenerateContent` canned responses (used
    in Phase 2).
  - Controllable via headers/query/env: injected latency (fixed + jitter), error rate (% of 5xx),
    forced total-outage window, and `Retry-After` emission.
- **AC:**
  - Can start the mock and `curl` a buffered chat completion + an SSE stream successfully.
  - Setting error-rate=1.0 makes it return 503 for every request; latency knob visibly delays responses.
  - Deterministic: same request + same config → same response (no wall-clock randomness that breaks tests).

### Issue 1.3 — Characterization tests for current behavior
- **Goal:** lock in today's behavior so the Phase 2 refactor is provably behavior-preserving.
- **What to do:**
  - Point the existing proxy at the mock upstream (via `OPENAI_UPSTREAM_BASE`).
  - Write table tests covering: happy-path buffered, happy-path stream, 429 rate-limit shedding,
    retry-then-succeed, circuit-breaker trip under sustained 5xx, dedup coalescing of identical
    concurrent requests, usage-token extraction.
- **AC:**
  - All behaviors above have a passing test.
  - Tests run against the mock only (no network, no keys).
  - `make test` green.

---

## Phase 2 — Provider abstraction layer

**Why:** to be "more than a passthrough," the gateway must translate between the stable OpenAI
client contract and genuinely different provider wire formats. This is the core engineering artifact.

### Issue 2.1 — Define the `Provider` interface
- **Goal:** a single seam that all provider-specific logic lives behind; resilience pipeline untouched.
- **What to do:**
  - New package `provider/`. Define:
    ```
    type Provider interface {
        BuildRequest(ctx, openaiReqBody []byte, stream bool) (*http.Request, error)
        TranslateResponse(nativeBody []byte) (openaiBody []byte, err error)
        TranslateStreamChunk(nativeChunk []byte) (openaiSSE []byte, err error)
        ExtractUsage(nativeBody []byte) (prompt, completion int64, ok bool)
        MapError(status int, nativeBody []byte) (normStatus int, msg string)
        PriceOf(model string) (inPer1K, outPer1K float64)
    }
    ```
  - Define OpenAI request/response Go structs (the internal canonical shape) in `provider/openai_types.go`.
- **AC:**
  - Package compiles; interface + canonical types defined and documented with doc comments.
  - No wiring into `proxy.go` yet (pure addition).

### Issue 2.2 — Implement `openaiCompatProvider`
- **Goal:** generalize today's hard-coded forwarding into the first Provider impl (identity-ish).
- **What to do:**
  - Port logic from `buildUpstreamRequest` (`proxy.go:200`) and `recordUsage` (`proxy.go:224`).
  - Bearer auth injection; `/v1` path handling; usage from `usage.{prompt,completion}_tokens`.
  - Covers OpenRouter / Groq / Together / vLLM / Gemini-compat by config alone.
- **AC:**
  - Unit tests: given an OpenAI request → produces correct upstream request (URL, headers, body).
  - `ExtractUsage` returns correct token counts from a canned OpenAI response.
  - Round-trips a mock-upstream call end-to-end in a test.

### Issue 2.3 — Implement `geminiNativeProvider`
- **Goal:** prove real translation against a genuinely different schema (the credibility centerpiece).
- **What to do:**
  - Translate OpenAI `messages` → Gemini `contents` + `systemInstruction`; map roles
    (`assistant`→`model`); map tool/function calls → `functionDeclarations` / `functionCall`.
  - Translate Gemini response + SSE chunks back to OpenAI shape.
  - Usage from `usageMetadata`; error mapping from Gemini error envelope.
  - Build against the **public documented `generateContent` schema**; validate with the mock upstream.
- **AC:**
  - **Golden-file tests** both directions: OpenAI req → `generateContent` JSON, and native
    response/SSE → OpenAI JSON (fixtures checked into `provider/testdata/`).
  - Tool-call translation covered by at least one golden test.
  - Streaming: a sequence of native chunks translates to a valid OpenAI SSE sequence ending in `[DONE]`.

### Issue 2.4 — Model→provider routing + YAML `model_list`
- **Goal:** pick a provider per request by model name, LiteLLM-style, from config (not hard-coded).
- **What to do:**
  - Add a `config.yaml` `model_list` (model name → provider type, upstream base, key ref, pricing).
  - Extend `Config` (`config.go:15`) to load + validate this file; keep env for secrets (key refs).
  - Add a provider registry that resolves `requestMeta.Model` (`proxy.go:50`) → `Provider` instance.
- **AC:**
  - A model mapped to `gemini-native` routes through the Gemini adapter; one mapped to `openai-compat`
    routes through that adapter — proven by a test asserting which upstream got called.
  - Unknown model → clean 400 with a clear error (not a panic/500).
  - Invalid `config.yaml` → startup fails loudly with a helpful message.

### Issue 2.5 — Wire providers into the request path
- **Goal:** replace the hard-coded forwarding with the Provider seam; keep resilience above it.
- **What to do:**
  - In `serveBuffered` / `serveStreaming` (`proxy.go`), replace `buildUpstreamRequest` with
    `provider.BuildRequest` and add response translation (`TranslateResponse` / `TranslateStreamChunk`).
  - Replace `recordUsage` internals with `provider.ExtractUsage`.
  - Add a `provider` label to Prometheus vecs in `metrics.go`.
- **AC:**
  - All Phase 1.3 characterization tests still pass (behavior preserved for OpenAI-compat path).
  - The DocuMedAI backend chat flow works unchanged through the proxy (`LLM_PROXY_BASE_URL`) against
    the mock — regression guard on the OpenAI contract.
  - `/metrics` now shows `provider` label populated.

### Issue 2.6 — Document providers & Anthropic exclusion in README
- **Goal:** make the scope decision explicit and defensible.
- **What to do:** update `README.md`: supported providers, `model_list` config example, and the
  reason Anthropic is deferred (interface-ready; two divergent adapters already prove the pattern;
  adds schema/test surface without architectural change).
- **AC:** README has a Providers section + a clearly written "Why not Anthropic (yet)" note + config example.

---

## Phase 3 — OpenTelemetry tracing

**Why:** a gateway lives in the request path, so distributed tracing is its natural observability.
Prometheus counters say *what*; traces say *why p99 was slow*.

### Issue 3.1 — Add OTel SDK + OTLP exporter
- **Goal:** emit traces without breaking existing metrics.
- **What to do:**
  - Add OTel Go SDK + OTLP/HTTP exporter to `go.mod`; init a tracer provider in `main.go`.
  - Config: `OTEL_EXPORTER_OTLP_ENDPOINT`, sampling ratio, service name; **disabled cleanly** if endpoint unset.
  - Add a Jaeger (or Tempo) service to `docker-compose.yml` for local viewing.
- **AC:**
  - With endpoint set, a request produces a trace visible in Jaeger UI.
  - With endpoint unset, service runs normally with zero tracing overhead / no errors.

### Issue 3.2 — Span the request pipeline
- **Goal:** one trace per client request with meaningful child spans.
- **What to do:** create child spans for rate-limit wait → dedup lookup → breaker gate → each retry
  attempt → provider translate → upstream call → stream duration. Attach attributes (model, provider,
  status, attempt#, dedup-hit).
- **AC:**
  - A single client request shows the full span tree in Jaeger.
  - A request that retries shows multiple upstream-call child spans.
  - A rate-limited request shows time spent in the rate-limit-wait span.

---

## Phase 4 — ClickHouse usage log & cost analytics

**Why:** durable, high-write, analytical record of every request → per-key spend & cost dashboards
(what LiteLLM leans on Postgres for and struggles with at volume). Also the sink for benchmark data.

### Issue 4.1 — ClickHouse schema + client
- **Goal:** a table and a writer.
- **What to do:**
  - Add ClickHouse to `docker-compose.yml`; add the Go ClickHouse driver to `go.mod`.
  - Design an append-only `usage` table: ts, request_id, model, provider, prompt_tokens,
    completion_tokens, cost_usd, latency_ms, upstream_status, retries, dedup_hit, api_key_hint.
  - Implement a writer with **async batched inserts** (buffer + flush by size/interval).
- **AC:**
  - Table created via a migration/init script on stack up.
  - A unit/integration test inserts a batch and reads it back.
  - Writer never blocks the request path (fire-and-forget with bounded buffer; drops + counts on overflow).

### Issue 4.2 — Emit a usage row per request + cost calc
- **Goal:** every completed request produces exactly one accurate row.
- **What to do:**
  - After `finish` (`proxy.go`), compute cost from `provider.PriceOf(model)` × tokens and enqueue a row.
  - Add a Prometheus counter for cost and for rows dropped on buffer overflow.
- **AC:**
  - N requests → N rows in ClickHouse (verified in a test) with correct tokens/cost/provider/latency.
  - A sample analytical query (spend per model, per api-key) returns sensible numbers.

---

## Phase 5 — Horizontal scalability (multi-replica correctness)

**Why:** dedup is currently in-process `singleflight` (`dedup.go`) — **wrong across replicas**.
Making the gateway stateless + correct at N replicas is the production-readiness milestone.

### Issue 5.1 — Redis-backed cross-replica dedup
- **Goal:** identical concurrent requests coalesce even when they hit different replicas.
- **What to do:**
  - Replace/augment `singleflight` with a Redis-based in-flight marker (SETNX lock on request hash +
    result publish/await, with TTL + safe fallback to solo execution on Redis failure).
  - Keep fail-open behavior (Redis down → still serve, no coalescing).
- **AC:**
  - Two replicas receiving the same request concurrently → only one upstream call (verified with the
    mock upstream's call counter).
  - Redis down → both still succeed independently (fail-open), asserted by a test.

### Issue 5.2 — Confirm rate-limit is already cross-replica; run multi-replica
- **Goal:** prove the stack scales horizontally.
- **What to do:**
  - Verify token-bucket (`ratelimit.go`) is Redis-shared across replicas (it is by design — add a test).
  - Add a multi-replica compose profile: 2+ proxy replicas behind nginx (or K8s manifests + HPA).
- **AC:**
  - With 2 replicas + shared Redis, aggregate rate limit is respected (not doubled) — asserted by a test/bench.
  - Load balancer distributes traffic; killing one replica mid-load does not fail the client stream set.

---

## Phase 6 — Benchmark framework (the thesis)

**Why:** the reliability claims are only credible if measured. This phase produces the charts that
answer "why pull this repo." Reuse the `backend/vector_database_tests/` discipline: **open-loop,
fixed-QPS, coordinated-omission-aware latency measured from scheduled send time.**

### Issue 6.1 — k6 open-loop load scripts
- **Goal:** a fixed-QPS driver that measures latency from *scheduled* send time (no coordinated omission).
- **What to do:**
  - Add `bench/` with k6 scripts using constant-arrival-rate executor; support buffered + SSE.
  - Parameterize target arm (direct / gateway / LiteLLM), QPS, duration, payload.
- **AC:**
  - k6 run against the mock upstream produces p50/p95/p99 + success rate + throughput.
  - Latency is measured from scheduled time, not send time (documented + verified in the script).

### Issue 6.2 — Three-arm comparative rig
- **Goal:** apples-to-apples: direct-to-mock, this-gateway→mock, LiteLLM→mock.
- **What to do:**
  - Add a LiteLLM proxy service (compose) pointed at the same mock upstream.
  - A runner script that executes all three arms with identical load + fault config, writes per-run JSON
    (config + results), like the vector lab.
- **AC:**
  - One command produces comparable JSON for all three arms under the same conditions.
  - Results record the exact config that produced them (reproducible).

### Issue 6.3 — Fault-injection scenarios + headline charts
- **Goal:** produce the specific evidence for each value claim.
- **What to do:** run and chart:
  - **A. Overhead:** added p50/p95/p99 = gateway − direct at matched QPS; throughput ceiling vs LiteLLM.
  - **B. Resilience:** 20% injected 503s → client success rate (direct vs gateway); latency-through-an-
    outage-window showing breaker trip → fail-fast → recovery.
  - **C. Cost saved:** duplicate-heavy workload → dedup hit-rate → upstream calls avoided.
  - **D. Graceful degradation:** past capacity → clean 429 + `Retry-After`, no collapse.
  - **E. Feature cost:** overhead delta with rate-limit/dedup/tracing on vs off.
  - Generate 3–4 charts + a Grafana dashboard.
- **AC:**
  - Each scenario yields a reproducible number/chart.
  - README top shows: overhead-vs-direct/LiteLLM, success-under-fault, latency-through-outage, throughput.
  - The north-star story sentence is filled in with real measured numbers.

---

## Phase 7 — Live smoke test (optional, requires real keys)

### Issue 7.1 — Real provider smoke test
- **Goal:** confirm the adapters work against real endpoints (not just mock).
- **What to do:** with real Gemini + OpenRouter keys, run a small scripted set of buffered + streaming
  chat completions through both adapters. Keep it out of CI (manual, key-gated).
- **AC:**
  - Both providers return valid OpenAI-shaped responses for buffered + streaming.
  - Usage tokens + cost recorded correctly in ClickHouse for real responses.

---

## Explicitly deferred (documented decisions, not gaps)

| Item | Why deferred | When it becomes worth it |
|------|--------------|--------------------------|
| **Anthropic provider** | Two divergent adapters already prove the interface; adds test surface, not architecture | When you want broader coverage; slots in as a new `Provider` impl, no refactor |
| **Kafka event pipeline** | Direct batched ClickHouse insert is simpler and fast enough | When billing events must survive ClickHouse downtime across many replicas |
| **gRPC** | Breaks the drop-in OpenAI compat value prop; one internal binary, no mesh | Never for client-facing; only if a real internal service split appears |
| **K8s operators / CRDs / Helm as a product** | Scope creep; goal is to *prove* horizontal scale, not build a platform | If this becomes a distributed product with many tenants |
| **Semantic cache** | Risk of wrong-answer cache hits | After exact-match request cache lands; make it opt-in |

---

## Suggested order of work (dependencies)

```
Phase 0 (learn) ─> Phase 1 (harness) ─┬─> Phase 2 (providers) ─┬─> Phase 3 (OTel)
                                      │                        ├─> Phase 4 (ClickHouse)
                                      │                        └─> Phase 5 (multi-replica)
                                      └───────────────────────────> Phase 6 (benchmark) ─> Phase 7 (live smoke)
```
Phase 0 (learn) gates everything; Phase 1 (harness) gates all build work. Phase 6 depends on Phase 1's
mock upstream and benefits from Phases 2/5 being done (so the benchmark reflects the real gateway).
Phases 3/4/5 are independent of each other and can be done in any order after Phase 2.
