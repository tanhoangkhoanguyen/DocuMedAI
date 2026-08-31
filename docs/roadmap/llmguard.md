# LLMGuard ROADMAP

> **Stale in places.** Written before the Vertex AI migration, so Phase 0's code tour still
> describes the OpenAI-compat passthrough: `UPSTREAM_API_KEY`, `OPENAI_UPSTREAM_BASE`,
> `LLM_PROXY_BASE_URL` and `get_llm_base_url()` no longer exist. Those are replaced by
> `GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION` + ADC, and the passthrough is now a
> provider adapter (`provider/`). Phase 2's provider abstraction is **implemented**; its
> first adapter is Vertex.
>
> **Phase 1 is now complete.** `make test` / `make lint` run in CI on every PR touching
> `backend/llmguard/**`; `mockupstream/` exists with its own tests pinning the determinism
> Phase 6 depends on; and the proxy pipeline has characterization tests sitting beside
> the code they exercise (`retry_test.go`, `proxy_*_test.go`, …). See
> **Findings** below for what those tests turned up.

This is the single roadmap for `llmguard`. It has two parts:

- **Phase 0 — Learn what exists.** The current code was written fast ("vibe-coded"); first
  **understand, verify, and learn** it — a guided tour of the code that already exists, broken into
  small issues. Do this before building anything new.
- **Phases 1–6 — Build what's next.** Turn the current single-upstream reliability gateway into a
  **production-grade, reliability-first LLM gateway** — an OpenAI-compatible, multi-provider proxy
  whose reliability claims are *proven by benchmark*. Implemented **one issue at a time, by hand**.

**North-star story (what the finished project claims)**
> "Reliability-first LLM gateway (Go): OpenAI-compatible multi-provider API with circuit breaking,
> provider-aware retries, and cross-replica breaker state. Open-loop, coordinated-omission-aware benchmark
> proves < N ms p99 overhead and 99%+ client success under 20% upstream fault injection (vs ~80%
> direct), validated against a direct-to-upstream baseline."

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
**admit → rate limit → circuit breaker → retry**, gets its dummy key swapped for the real upstream
key, is forwarded to an OpenAI-compatible endpoint, and the response (or a stream) is sent back —
with Prometheus metrics recorded throughout.

**Request pipeline (memorize this order — every file maps to a box):**
```
/v1/*  →  Admit       →  [shed at capacity]          admission.go
       →  Rate limit  →  [stream? pass-through]      ratelimit.go / proxy.go
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
> **REMOVED.** `dedup.go` and its test no longer exist. The deduper only coalesced
> byte-identical *concurrent* bodies, which real chat traffic almost never produces —
> the conversation history plus the new user turn differ on every request — so it cost
> a direct `golang.org/x/sync` dependency, a Prometheus counter and 173 test lines for
> close to nothing. The body hash survives as `requestSeed` in `proxy.go`, which the
> retry loop still needs for its jitter seed. This issue is kept as a record of what
> the code did; read it as history, not as a description of the tree.
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
> **PARTLY REMOVED.** The dedup layer is gone (see Issue C.3), so the real nesting is now
> `breaker.Execute( doWithRetry( forwardBuffered ))` and there is no `shared` flag and no
> `dedupHits` counter. Everything else in this issue still describes the tree — read the
> dedup half as history.
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
  - `newMetricsWith` — `promauto` auto-registers on a caller-supplied registry, which is how the
    tests avoid colliding on the global one. Note the names and the labels each carries.
  - Cross-reference each metric to its call site: `requests` in `writeJSON` and on the streaming
    header; `rateLimited` and `shed` on the two refusal paths; `streamAborts` where a stream is
    cut; `inFlight` in `admission.go`; `circuitState` in `newBreaker` (seeded there, then written
    only by `OnStateChange`).
  - Six series is the whole surface, and the absences are deliberate — latency, retries and tokens
    moved to the trace store or were dropped outright (Phase 4, Issue 4.2).
- **Verify (hands-on):** exercise each path (a normal request, a rate-limited burst, a breaker trip,
  a stalled stream) and confirm the matching metric moves in `/metrics`. This is the best single
  test that you understand the whole system — each metric is a witness to a code path.
- **Done when:** for any metric name in `/metrics`, you can name the exact function+line that changes it.

### Issue D.2 — `README.md` vs reality
- **Goal:** confirm the docs match the code you now understand, and note drift.
- **What to check:** read `README.md` end to end against what you learned. Check the endpoints table,
  the config table (defaults must match `config.go`), the path-stripping note, and the "deferred
  features" list. Two of those are settled rather than pending: cross-replica dedup is WON'T DO
  (Issue 5.1) and billing is out of scope (Phase 4) — check the README says so too.
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

## Findings

Everything the Phase 1 harness turned up. **Fixed** rows cite the commit; **open** rows name
the phase that should absorb them. A finding is only "handled" if some phase's acceptance
criteria actually forces someone to touch that line — several below have no such owner, which
is why they are written down rather than assumed.

### Fixed

| File | What was wrong | Impact | Fixed in |
|------|----------------|--------|----------|
| `retry.go` `doWithRetry` | The non-retryable early exit read `err == nil && !isRetryable(res.status)`, but `TranslateResponse` returns an `*UpstreamError` for **every** non-2xx, so `err` was never nil on failure and the guard was unreachable dead code. | A 400 or 401 burned the full `RetryMax` (4) upstream calls plus 3 backoff sleeps — wasted quota and latency on a request that could never succeed. | `34cda1a` |
| `retry.go` `newBreaker` | `gobreaker`'s `IsSuccessful` was unset, so its default counted every non-nil error as an upstream failure — including a 400 caused entirely by the caller's own malformed request. | 10 bad requests from one buggy client opened the breaker for **every** user for `CircuitOpenFor` (20s). A client-side bug became a service-wide outage. | `ac0f0ef` |
| `retry.go` `doWithRetry` | Four distinct information losses: last-error-wins overwrote a vendor error with a later transport failure; the `ctx.Done` exit returned `ctx.Err()` unconditionally; `Retry-After` was uncapped; only the delta-seconds form was parsed. | A real 429 degraded into a generic 503 "upstream unavailable"; `Retry-After: 86400` would park a request for a day holding a singleflight slot; Google's HTTP-date form was ignored. | `c85aac3` |
| `retry.go` `doWithRetry` | Trailing `errors.New("exhausted retries")` was unreachable — a success returns immediately, so `lastErr` is always set on fall-through. Plus a stale comment describing pre-Vertex behavior. | Dead code implying a failure mode that cannot occur. | `634d05d` |
| `proxy.go` `forwardBuffered`, `serveStreaming` | `io.ReadAll` on the upstream response body was unbounded. The buffered path holds the whole body in memory so a failed attempt can be replayed. | A broken, misconfigured or hostile provider could exhaust the gateway's memory — a denial-of-service vector on a service that sits in the request path. | `882d326` — `readUpstreamBody` caps at 10 MiB and reads one byte past the limit so an oversized response is a named error rather than a silent truncation |
| `proxy.go` `serveBuffered` | `dedupHits` was incremented **before** the error check, so callers sharing a flight that ended in a 500, a breaker-open 503 or a transport error were all recorded as dedup hits. | The counter reported a thundering herd as absorbed when in fact every caller failed — nothing was shared except the failure. | this PR — the increment moved past every error branch |
| `proxy.go` `serveStreaming` | On the streaming path an upstream failure only logged. The client received **HTTP 200 with a completely empty body** — no error envelope, no SSE frames, no `[DONE]`. | Indistinguishable from a successful empty completion. | this PR — split at the `WriteHeader` commit: a pre-header failure returns the vendor's real status and error envelope; a post-header one keeps every delivered frame and **appends** an in-band error frame plus `[DONE]`, so partial output survives and the client still learns the stream ended badly |
| `ratelimit.go` `Acquire` | The deadline was checked *before* the sleep and the sleep was a fixed poll interval, so `Acquire` could return up to one full interval past `RateWaitMax`. | `RateWaitMax` is the knob an operator turns to bound tail latency; overshooting it silently defeats its purpose. Invisible at the default 480 RPM (31ms), 1.25s at 12 RPM. | this PR — the poll is clamped to the remaining wait, after the 20ms floor so the floor cannot re-inflate it |
| `retry.go:57-100` | The breaker's **trip** was pinned by `circuitbreaker_test.go`, but its recovery — `CircuitOpenFor` elapsing → half-open probe → closed — was never asserted. | Incomplete coverage, not a bug. **Phase 6.3 scenario B charts "breaker trip → fail-fast → recovery"** and would have been charting untested behavior. | this PR — `TestCircuitBreakerRecovers` flips the upstream healthy through the intercept hook so one breaker instance lives through both regimes, and guards `realDefaults().CircuitOpenFor == 20s` so the test's shortened override cannot mask a production change. No production code changed. |

### Open

| File:Line | What I observed | Expected? | Note / follow-up |
|-----------|-----------------|-----------|------------------|
| `mockupstream/chaos.go:76-78` | `Jitter` changes the **failure verdict**, even though it is correctly excluded from `fingerprint()`. `decide()` draws jitter *conditionally* on `Jitter > 0`, consuming one number from the per-request stream and shifting the failure roll that follows. Measured: **21 of 40 nonces flip** at an unchanged `error_rate=0.5`. | No — the comment at `chaos.go:74-75` claims the fixed draw order prevents exactly this. It only holds for knobs that *always* draw; the error-rate roll already does this correctly (`chaos.go:84`). | **Open with a documented constraint, not an unowned bug.** Determinism still holds *within* a config; what breaks is comparability *across* configs differing only in jitter. **The rule — hold `Jitter` fixed across arms of a comparison** — is now stated in [`mockupstream/README.md`](../../backend/llmguard/mockupstream/README.md) → *Jitter shifts the failure verdict* and as a precondition on **Issue 6.3**, its consumer. Not fixed here because drawing jitter unconditionally changes every existing seeded value and invalidates any captured baseline — a Phase 6 decision about baselines. Pinned by `TestJitterShiftsFailureVerdictQuirk`. |
| `proxy.go` `serveBuffered` | **Moot — dedup removed** (see Issue C.3). `dedupHits` counted **every** flight participant, because `singleflight` reports `shared=true` to the leader that did the work too. 8 concurrent identical requests → 8 hits, though only 7 upstream calls were avoided. | Off by one per flight *as a cost meter*. | **Accepted, not merely tolerated.** LLMGuard's concern is reliability, not spend, and the counter is a **coalescing signal** — "did a thundering herd collapse into one upstream call" — which the current count answers. An exact callers-saved figure buys precision nobody reads. If **Phase 6.3 scenario C** charts this as "cost saved", either subtract one per flight at chart time or rename the metric. Pinned in `dedup_test.go`. |
| `provider/vertex/vertex.go` | Buffered completions ship with `id: ""` and `created: 0`; the Vertex adapter never populates them. | No — OpenAI clients that key off response id see an empty string. | Cosmetic but contract-visible. **Phase 2 Issues 2.1-2.3** rebuild this layer with golden-file tests; fix there. Pinned in `proxy_buffered_test.go`. |

---

## End of Phase 0

Once every Phase-0 issue's "Done when" is checked, you can explain the whole proxy to someone else —
that is the gate to building. Phases 1–6 below extend it (multi-provider, OpenTelemetry, ClickHouse,
cross-replica breaker state, benchmark). From here, each issue uses **Goal / What to do / AC**.

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
    retry-then-succeed, circuit-breaker trip under sustained 5xx, usage-token extraction.
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
  - Define OpenAI request/response Go structs (the internal canonical shape) in `provider/schema.go`.
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
    (`assistant`→`model`). (Tool/function-call translation was built here and later
    removed — the gateway proxies user→model completions only and refuses `tools` with a 400.)
  - Translate Gemini response + SSE chunks back to OpenAI shape.
  - Usage from `usageMetadata`; error mapping from Gemini error envelope.
  - Build against the **public documented `generateContent` schema**; validate with the mock upstream.
- **AC:**
  - **Golden-file tests** both directions: OpenAI req → `generateContent` JSON, and native
    response/SSE → OpenAI JSON (fixtures checked into `provider/testdata/`).
  - ~~Tool-call translation covered by at least one golden test.~~ (Removed with tool calling.)
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
  - Add an OTLP collector service to `docker-compose.yml` (`la-otel-collector`, on the
    `observability` profile).
- **AC:**
  - With endpoint set, a request produces a trace in the store behind the collector.
  - With endpoint unset, service runs normally with zero tracing overhead / no errors.

### Issue 3.2 — Span the request pipeline
- **Goal:** one trace per client request with meaningful child spans.
- **What to do:** create child spans for rate-limit wait → each retry attempt → upstream call →
  stream duration. Attach attributes (model, provider, status, attempt#, refusal reason, abort
  reason). No span for provider translation — a few-µs unmarshal costs as much to observe as to
  perform — and none per SSE frame, which is the exhaustion vector `maxUpstreamBody` exists to stop.
- **AC:**
  - A single client request yields the full span tree, queryable by `TraceId`.
  - A request that retries shows multiple upstream-call child spans.
  - A rate-limited request shows time spent in the rate-limit-wait span.

---

## Phase 4 — ClickHouse trace store

**Why:** Prometheus holds pre-aggregated series, so a latency quantile is interpolated between
pre-declared bucket bounds — at LLM latencies the widest bucket spans seconds, which hides any
change smaller than the bucket. Phase 6 has to measure differences finer than that, so the durations
have to be stored exactly. Traces already carry them; what was missing was somewhere durable to put
them.

**Not cost analytics.** The original plan for this phase was a `usage` table with `cost_usd` and
per-key spend dashboards. That is dropped: LLMGuard is a reliability gateway, and `config.yaml`'s
`pricing` block stays recorded-but-unread for a later consumer, as its own comment says. Nothing
here computes money.

### Issue 4.1 — Collector + ClickHouse behind OTLP
- **Goal:** spans land somewhere durable and queryable, without the gateway naming a backend.
- **What to do:**
  - Add `la-clickhouse` and `la-otel-collector` to `docker-compose.yml` on the `observability`
    profile; point `OTEL_EXPORTER_OTLP_ENDPOINT` at the collector.
  - Configure the collector's ClickHouse exporter in `observability/otel-collector.yaml`. Keep the
    Go code free of any ClickHouse dependency, so swapping stores is a config change.
  - Give the collector its own ClickHouse user (`observability/clickhouse-user.xml`) — the image
    restricts `default` to localhost — and create the database up front
    (`observability/clickhouse-init.sql`), since that user has no `CREATE DATABASE` grant.
- **AC:**
  - A request with tracing on produces rows in `otel.otel_traces`.
  - `quantileExact` over `Duration` returns p50/p95/p99 per provider from exact nanosecond values.
  - Tracing off (endpoint unset) leaves the stack working with the store absent.

### Issue 4.2 — Move off the metrics the store answers better
- **Goal:** one place per question, and no metric whose number the store already reports more
  precisely.
- **What to do:**
  - Drop `llmguard_request_duration_seconds` (bucketed, and it observed **every** response including
    refusals, so heavy shedding dragged its p50 *down* while the gateway was failing),
    `llmguard_retries_total` (`GROUP BY TraceId` gives the distribution, not just a total) and
    `llmguard_tokens_total` (no consumer — see the scope note above).
  - Keep what a trace cannot hold or cannot be trusted to: `circuit_state` and `in_flight` are
    state, with no row to insert, and `in_flight` is the only signal that predicts shedding before
    `shed_total` moves. Keep `requests_total`, `shed_total`, `rate_limited_total` and
    `stream_aborts_total` — each is a refusal sharing a status code with something else, and a
    counter is incremented in-process where a span can be dropped silently.
- **AC:**
  - `/metrics` exposes exactly the six kept series; the suite passes with the assertions on removed
    metrics gone rather than rewritten.
  - `requests_total` equals `count(DISTINCT TraceId)` in ClickHouse under load — the check that the
    async export is not dropping spans.

### Issue 4.3 — Benchmark queries in the README
- **Goal:** the numbers Phase 6 reports are reproducible by someone who did not run the benchmark.
- **What to do:** document the p50/p95/p99 query, how to reach the store
  (`clickhouse-client -d otel`), and the dropped-span check. Record that the collector's queue sizes
  stay at their defaults until a real benchmark says what the load is.
- **AC:** the README's queries run as written against a live stack.

---

## Phase 5 — Horizontal scalability & self-protection

**Why:** two separate gaps, both about the gateway holding up rather than the upstream.

*Across replicas:* the circuit breaker is per-process (`retry.go`), which is **wrong at N
replicas**. Making the gateway stateless and correct at N is the production-readiness milestone.
Issue 5.1b. (Issue 5.1, cross-replica dedup, is WON'T DO — in-process dedup was removed instead.)

*Within one replica:* retry and the breaker protect the upstream **from** LLMGuard; nothing
protects LLMGuard from its callers. Concurrency, not arrival rate, is what maps to memory, and
nothing bounds it — so the gateway is currently a candidate for being the outage. Issue 5.3.

### Issue 5.1 — Redis-backed cross-replica dedup
> **WON'T DO.** In-process dedup was removed instead (see Issue C.3): it coalesced
> byte-identical concurrent bodies, which chat traffic almost never produces. Extending
> a mechanism that rarely fires across replicas would add a Redis round trip to every
> request's hot path to win the same nothing.
- **Goal:** identical concurrent requests coalesce even when they hit different replicas.
- **What to do:**
  - Replace/augment `singleflight` with a Redis-based in-flight marker (SETNX lock on request hash +
    result publish/await, with TTL + safe fallback to solo execution on Redis failure).
  - Keep fail-open behavior (Redis down → still serve, no coalescing).
- **AC:**
  - Two replicas receiving the same request concurrently → only one upstream call (verified with the
    mock upstream's call counter).
  - Redis down → both still succeed independently (fail-open), asserted by a test.

### Issue 5.1b — Cross-replica circuit-breaker state
- **Why:** the breaker in `retry.go` is per-process state. It is per replica, so at
  N replicas an upstream absorbs **N × `CircuitMinReqs`** doomed requests before anything trips, and
  a replica restarted mid-outage begins from a clean slate and hammers a provider the others already
  know is down. Both defeat the point of having a breaker.
- **Design decision — share the trip SIGNAL, not the counters.** Shared counters would put a Redis
  round trip on every request and make Redis a hard dependency of a component that currently has
  none. A trip is one fact with a natural lifetime (`CircuitOpenFor`) and needs no consensus: "this
  provider was found down recently" is safe for any replica to act on, and the local breaker still
  governs recovery through its own half-open probe.
- **What to do:**
  - Publish `llmguard:breaker:open:<provider>` with TTL = `CircuitOpenFor` when a local breaker opens.
    TTL rather than an explicit clear, so a replica that crashes while holding it cannot leave the
    others shedding forever.
  - Read it before dispatch; fail fast with 503 when another replica has it set. It runs **after**
    admission control and the rate limiter: the read costs a Redis `EXISTS`, so it is not paid until
    the request is known to have both a capacity slot and a token.
  - Cache **only the positive** reading. A cached negative would delay this replica's entry into an
    outage by a whole trust window — exactly the lateness the shared flag removes.
  - Fail open on any Redis error, matching `ratelimit.go`: a Redis outage must never be able to shed
    traffic on its own.
- **AC:**
  - Replica A trips → replica B sheds without collecting its own `CircuitMinReqs` failures.
  - A trip on one provider does not shed traffic bound for another.
  - Redis unreachable → nothing is shed by the sharer; the local breaker still protects upstream.
  - The flag expires on its own, so a crashed replica cannot pin the others open.

### Issue 5.3 — Admission control & backpressure
- **Why:** every existing protection (retry, breaker) shields the **upstream** from LLMGuard.
  Nothing shields **LLMGuard from its own callers**. The rate limiter looks like it should, but it
  bounds the arrival *rate* (RPM), not the number of requests running concurrently — and those
  diverge exactly when it matters. At 480 RPM with 20s calls, ~160 are legitimately in flight; if
  upstream slows to 60s the same admitted rate produces ~480, each holding a goroutine, a buffer up
  to `maxUpstreamBody` (10 MiB), and an upstream connection. Arrival rate never signals that, so the
  limiter keeps admitting while memory runs out. A gateway whose claim is *reliable LLM calls* must
  not become the new failure: refusing some requests beats an OOM that fails all of them, including
  the ones already half-served. This is also what makes **Scenario 6.3-D** testable — it currently
  has no enforcing code.
- **What to do:**
  - A `MAX_IN_FLIGHT` counting semaphore, acquired **non-blocking**. Queueing would be a second rate
    limiter and a worse one: a request parked waiting still owns a goroutine and its buffers, growing
    the very resource the ceiling bounds while adding latency to a caller already being refused.
  - Refuse with **429 + `Retry-After`**, not 503. The upstream is healthy and the request is
    well-formed — the gateway is out of capacity. 503 would tell clients the provider is down and
    send fail-over traffic away from a healthy upstream.
  - Place it **before** the rate limiter (which can block for `RateWaitMax`) and **after**
    `provider.For`, so the wait is inside the ceiling and a shed request is attributed to a real route.
  - Metrics: an unlabelled `llmguard_in_flight` gauge (the semaphore is one process-wide pool, so a
    per-provider split would match no single limit) and `llmguard_shed_total{provider,model}` kept
    **separate from `rate_limited_total`** — both return 429 but one is a caller exceeding quota and
    the other is an operator capacity problem.
  - Default `MAX_IN_FLIGHT=256` = `(RPM/60) × p95_seconds × 1.5` at 480 RPM / 20s p95, so a
    bucket-legal burst is never shed; it engages only when requests drain slower than they arrive, or
    when Redis is down and the limiter is failing open. Calibrate for real in Phase 6.
  - Add `IdleTimeout` to the HTTP server. A client that opens connections and goes quiet pins a
    goroutine and socket each, and admission control cannot see it — those requests already finished.
  - **`WriteTimeout` stays unset, as a decision.** It is an absolute deadline from the start of the
    response, so any value low enough to cut off a hung SSE reader also truncates healthy long
    streams. The real fix is a per-write deadline refreshed on each flushed frame
    (`http.ResponseController.SetWriteDeadline`), which measures time since the last successful
    write. That touches the streaming loop and belongs in its own change.
- **AC:**
  - Past the ceiling → clean 429 + `Retry-After`, and the shed request never reaches upstream.
  - Slots are returned on success, on error, and on the streaming path's early failures — a leaked
    slot would ratchet capacity down to zero over the process's lifetime.
  - Successes + sheds account for every caller under concurrent load; nothing dropped or
    double-counted.
  - `MAX_IN_FLIGHT=0` restores unbounded behavior.
  - Overload yields no OOM and no p99 regression for admitted requests (measured in Phase 6).

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
  - Parameterize target arm (direct / gateway), QPS, duration, payload.
- **AC:**
  - k6 run against the mock upstream produces p50/p95/p99 + success rate + throughput.
  - Latency is measured from scheduled time, not send time (documented + verified in the script).

### Issue 6.2 — Comparative rig
> **LiteLLM arm: WON'T DO.** LiteLLM is a routing and multi-provider layer; LLMGuard is a
> reliability gateway. Benchmarking them against each other compares the wrong axis — the
> overhead number would be real and the conclusion drawn from it would not, because neither
> tool is trying to do what the other does. The direct-vs-gateway arms answer the question
> that is actually being asked ("what does this hop cost"), and the resilience scenarios in
> 6.3 answer the one only this gateway can.
- **Goal:** apples-to-apples: direct-to-mock vs this-gateway→mock.
- **What to do:**
  - A runner script that executes both arms with identical load + fault config, writes per-run
    JSON (config + results), like the vector lab. `bench/sweep.sh` does this per QPS step.
- **AC:**
  - One command produces comparable JSON for both arms under the same conditions.
  - Results record the exact config that produced them (reproducible).

### Issue 6.3 — Fault-injection scenarios + headline charts
- **Goal:** produce the specific evidence for each value claim.
- **Precondition — hold `Jitter` fixed across arms.** `mockupstream`'s jitter draw is
  conditional, so enabling it shifts each request's *failure* verdict (21 of 40 nonces flip at
  `error_rate=0.5`). Two arms differing in jitter run against **different failure sets**, and the
  latency delta between them is partly a different mix of retried requests rather than the effect
  being measured. Vary the nonce set instead if you need a distribution. See
  [`mockupstream/README.md`](../../backend/llmguard/mockupstream/README.md) → *Jitter shifts the failure verdict* and the
  Findings row below.
- **What to do:** run and chart:
  - **A. Overhead:** added p50/p95/p99 = gateway − direct at matched QPS, plus the throughput ceiling.
  - **B. Resilience:** 20% injected 503s → client success rate (direct vs gateway); latency-through-an-
    outage-window showing breaker trip → fail-fast → recovery.
  - **C. Retry effectiveness:** injected transient failures → requests rescued by retry vs
    surfaced to the client. Read the attempt distribution from the trace store
    (`GROUP BY TraceId` over `upstream.attempt`), since no counter carries it. Replaces the
    dedup arm the original plan had — in-process dedup was removed (Issue C.3).
  - **D. Graceful degradation:** past capacity → clean 429 + `Retry-After`, no collapse. Depends on
    Issue 5.3 — without an in-flight ceiling there is nothing to shed and the arm measures an OOM.
    This is also where `MAX_IN_FLIGHT` gets calibrated against real p95 latency instead of the
    formula's estimate; chart `llmguard_in_flight` against the ceiling and `llmguard_shed_total`.
    Both are kept in Prometheus precisely because a trace has no row for either (Issue 4.2).
  - **E. Feature cost:** overhead delta with rate-limit/tracing on vs off. Tracing is the one
    that matters here: the SDK is installed only when the endpoint is set, so off is a genuine
    no-op and the delta is the real cost of exporting 100% of spans.
  - **F. Calibrate the load balancer.** `nginx/nginx.conf` carries four numbers that were taken
    as defaults rather than derived, and this is the arm that produces the evidence to set them.
    Two are only meaningful once `worker_processes` is decided, because `keepalive` and
    `worker_connections` are **per worker** and nginx defaults to one worker per CPU — so on an
    8-core host the pool is really 8 x `keepalive`:
    - `worker_processes` — unset today. Pin it, so the two below mean what they say.
    - `keepalive 32` — needs to exceed steady-state concurrency per replica, which scenario D
      measures.
    - `worker_connections 1024` — each SSE stream holds two connections here (client +
      upstream), so this is ~512 concurrent streams per worker. Compare against
      `MAX_IN_FLIGHT` x replica count once D has calibrated the ceiling.
    - `fail_timeout=10s` against `CIRCUIT_OPEN_FOR=20s` — nginx re-admits traffic to a replica
      whose own breaker is still open. Decide whether the two windows should match, or whether
      the earlier probe is wanted; either way it should be a decision.
  - Generate 3–4 charts from the per-run JSON. Latency figures come from the trace store
    (`quantileExact`), not from a histogram — see Phase 4 for why bucket bounds cannot resolve
    the differences these arms are measuring.
- **AC:**
  - Each scenario yields a reproducible number/chart.
  - README top shows: overhead-vs-direct, success-under-fault, latency-through-outage, throughput.
  - The north-star story sentence is filled in with real measured numbers.
  - Every nginx value in **F** is either changed with a measurement behind it or left alone with
    a comment saying which measurement says it is fine. A default that survives on purpose is a
    decision; one that survives because nobody looked is not.

---

## Phase 7 — Running against something real

### Issue 7.1 — Real provider smoke test
- **Goal:** confirm the adapters work against real endpoints (not just mock).
- **What to do:** with real Gemini + OpenRouter keys, run a small scripted set of buffered + streaming
  chat completions through both adapters. Keep it out of CI (manual, key-gated).
- **AC:**
  - Both providers return valid OpenAI-shaped responses for buffered + streaming.
  - Spans for the real calls land in `otel.otel_traces`, with per-attempt durations queryable
    by `TraceId`.

### Issue 7.2 — Route DocuMedAI's real traffic through LLMGuard
- **Goal:** the gateway serves traffic it did not author. Phase 6 runs against `mockupstream`
  under load the same author wrote; Issue 7.1 uses a real provider but only a handful of
  scripted calls. Neither exercises real concurrency from real users.
- **What to do:**
  - **Verify feasibility first — this is the real risk.** Neither current caller speaks
    OpenAI-compatible HTTP: `backend/utils/llm_config.py` uses `ChatVertexAI` (native SDK) and
    CrewAI reaches Vertex through litellm's `vertex_ai/` prefix. Confirm both can be pointed at
    `http://la-llmguard:8081/v1` before writing anything; record the blocker here if not.
  - Point the app's LLM config at LLMGuard with Vertex behind it — the `vertex` adapter
    already handles ADC, so credentials stay server-side.
  - Add `la-llmguard` to the app's compose dependency chain, and keep a documented env-var
    escape hatch so a gateway problem cannot take the app down during the trial.
  - Observe `llmguard_in_flight` against arrival rate under genuine load: when the upstream
    slows, arrivals stay flat while concurrency climbs, which is the failure mode admission
    control exists to bound. That relationship is invisible to k6, which measures client-side.
  - Record what observed concurrency says about `MAX_IN_FLIGHT=256` — it was chosen by
    reasoning, not measurement.
- **AC:**
  - A chat message from the frontend produces a reply whose upstream call went through
    LLMGuard (`llmguard_requests_total` increments for it).
  - Buffered and SSE paths both work unchanged from the user's point of view;
    `ci_tests/integration/api` still passes.
  - The escape hatch is documented and verified.
  - CLAUDE.md and `README.md` no longer say LLMGuard is "not in the request path" — that
    becomes false the moment this lands.

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
                                      └───────────────────────────> Phase 6 (benchmark) ─> Phase 7 (real traffic)
```
Phase 0 (learn) gates everything; Phase 1 (harness) gates all build work. Phase 6 depends on Phase 1's
mock upstream and benefits from Phases 2/5 being done (so the benchmark reflects the real gateway).
Phases 3/4/5 are independent of each other and can be done in any order after Phase 2. Issue 7.2
additionally wants Issue 5.3's in-flight ceiling in place, since observing concurrency against a
ceiling is most of the point.
