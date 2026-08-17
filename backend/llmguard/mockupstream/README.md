# mockupstream

A standalone, **deterministic** stand-in for an OpenAI-compatible or
Gemini-native LLM provider. It exists so the proxy's resilience behavior —
retry/backoff, the circuit breaker, singleflight dedup, `Retry-After` handling —
can be driven against a real upstream over a real socket, in its own process,
without spending money or depending on a provider being up.

## Why a library with a thin `cmd/`

`mockupstream` is a **package**, and `cmd/mockupstream` is a ~100-line binary on
top of it. That shape is deliberate: an in-process test fake can do

```go
srv := httptest.NewServer(mockupstream.New(mockupstream.DefaultConfig()))
```

and get **byte-identical** responses to the standalone process. The wire format
has exactly one definition, so the two can never drift apart. Nothing in the
package imports `testing` — a binary that imports `testing` picks up test flags
in its own flag set.

## Determinism

The same request under the same config produces byte-identical responses across
runs, on any machine. Two rules deliver that:

1. **Response content is a pure function of the request.** Ids, timestamps,
   token counts and generated text derive from the request bytes and static
   config only. There is no `time.Now()` anywhere in a response body —
   `created` is a fixed constant (`DefaultCreated`, 2025-01-01T00:00:00Z).
2. **Randomized behavior is seeded per request.** The error-rate roll and the
   latency jitter draw from an RNG seeded by
   `FNV-1a(method, path, body, config-fingerprint, nonce) ⊕ seed`.

Rule 2 is the subtle one. The obvious implementation — one package-level RNG
advanced per request — is reproducible only when requests arrive in the same
order, which stops being true the moment a benchmark opens a second connection.
Seeding from the request itself removes the shared state: a request draws the
same numbers regardless of what else is in flight.

Content and chaos draw from **separately seeded** streams. If they shared one,
turning jitter on would shift the stream position and silently change the words
in the reply — a latency knob altering response bytes.

### Jitter shifts the failure verdict — hold it fixed across a comparison

Determinism holds *within* a config: the same request under the same config is
always reproducible. What does **not** hold is comparability *across* configs
that differ only in `Jitter`.

`decide()` draws in a fixed order — jitter, then the failure roll — but the
jitter draw is **conditional** on `Jitter > 0` ([chaos.go:76-78](chaos.go)).
Turning jitter on consumes one number from the per-request chaos stream and
shifts the roll that follows, so the same request flips verdict at an unchanged
`error_rate`. Measured: **21 of 40 nonces flip** at `error_rate=0.5`. The fixed
draw order only protects knobs that draw *unconditionally* — which is why the
error-rate roll always draws, even at `ErrorRate >= 1`.

**The rule: hold `Jitter` fixed across arms of any comparison.** Two arms that
differ in jitter are running against **different failure sets**, so a latency
delta between them is partly a different mix of retried requests, not the effect
of jitter. That is a benchmark conclusion that looks clean and is wrong.

If you need to vary jitter and keep the failure set, vary the nonce set
deliberately (`X-Mock-Nonce`) and compare distributions rather than per-request
verdicts.

Fixing this means drawing jitter unconditionally and discarding it when
`Jitter == 0`. That changes every existing seeded value, so it invalidates any
baseline already captured — a decision about baselines, not a bug fix. Pinned
as-is by `TestJitterShiftsFailureVerdictQuirk`.

### The nonce escape hatch

Because identical requests share a verdict, `error_rate=0.5` against one
repeated body returns all-fail or all-succeed, not half. Set `X-Mock-Nonce`
(or `X-Request-Id`, which most load generators already send) to get a real
distribution. A bare `curl` without a nonce stays perfectly reproducible.

### The one exception

`/_mock/outage` is a wall-clock window and is therefore stateful and
time-based by definition. It is off unless a test switches it on, so the
reproducibility guarantee holds for every request outside a window.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/chat/completions` | OpenAI-compatible; buffered or SSE via the request's `stream` field |
| POST | `/v1beta/openai/chat/completions` | Same handler — Gemini's OpenAI-compat base |
| GET | `/v1/models` | Capability probe |
| POST | `/v1beta/models/{model}:generateContent` | Gemini native, buffered |
| POST | `/v1beta/models/{model}:streamGenerateContent` | SSE with `?alt=sse`, else a streamed JSON array |
| POST | `/v1beta/models/{model}:countTokens` | Prompt token estimate |
| GET | `/healthz` | Mock liveness — stays 200 during a simulated outage |
| POST | `/_mock/outage?duration=10s` | Open a total-outage window; `duration=0` clears |
| GET | `/_mock/config` | Effective config for the request as sent |

## Controls

Every knob is settable three ways, in increasing precedence: **env → query →
`X-Mock-*` header**. Headers win because a test often cannot control the URL
(the proxy builds it) but can always add a header.

| Knob | Env | Query | Header |
|---|---|---|---|
| Fixed latency | `MOCK_LATENCY` | `latency=500ms` | `X-Mock-Latency` |
| Jitter width | `MOCK_JITTER` | `jitter=800ms` | `X-Mock-Jitter` — also shifts the failure verdict; see [above](#jitter-shifts-the-failure-verdict--hold-it-fixed-across-a-comparison) |
| Inter-chunk delay | `MOCK_CHUNK_DELAY` | `chunk_delay=20ms` | `X-Mock-Chunk-Delay` |
| Stall after N chunks | `MOCK_STALL_AFTER` | `stall_after=3` | `X-Mock-Stall-After` — see [below](#stalling-a-stream-slow-loris-upstream) |
| Error rate `[0,1]` | `MOCK_ERROR_RATE` | `error_rate=1.0` | `X-Mock-Error-Rate` |
| Error status | `MOCK_ERROR_STATUS` | `error_status=429` | `X-Mock-Error-Status` |
| `Retry-After` secs | `MOCK_RETRY_AFTER` | `retry_after=3` | `X-Mock-Retry-After` |
| Base seed | `MOCK_SEED` | `seed=7` | `X-Mock-Seed` |
| Reply length | `MOCK_COMPLETION_TOKENS` | `completion_tokens=6` | `X-Mock-Completion-Tokens` |
| Fixed reply text | `MOCK_CONTENT` | `content=hello` | `X-Mock-Content` |
| Verdict nonce | — | — | `X-Mock-Nonce` / `X-Request-Id` |

Unparseable values fall back to the default rather than to zero, so a typo
degrades to sane behavior instead of silently disabling a knob.

### Stalling a stream (slow-loris upstream)

`stall_after=N` emits exactly N streamed chunks and then **stops sending without
closing** — the response stays open, indefinitely, until the client goes away.

This is the one failure the other knobs cannot express. An error rate produces an
error, an outage produces an error, and latency still terminates; a stall
produces a connection that looks perfectly healthy and never finishes. It is
therefore the only way to test an **inter-frame deadline**, because a consumer
that bounds a stream by total duration cannot tell a stall apart from a slow
generation — both are "still running after T seconds".

```bash
# Three frames, then silence. Ctrl-C to escape; it will not end on its own.
curl -N -X POST 'localhost:8090/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse&stall_after=3' \
  -H 'Content-Type: application/json' \
  -d '{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}'
```

Both streaming surfaces honor it, and both count **content** chunks — the OpenAI
surface's role-announcing opening chunk does not count, so `stall_after=3` means
the same thing on either. Like the delay knobs it is excluded from
`fingerprint()`: it truncates delivery but never rewrites a chunk that is sent,
so it must not shift the failure verdict.

## Running

```bash
# from backend/llmguard/
make build-mock            # -> ./bin/mockupstream(.exe)
make run-mock              # listens on :8090

go run ./mockupstream/cmd/mockupstream -addr :8090 -latency 200ms -jitter 100ms
```

Point the proxy at it: there is no upstream-base setting — the Vertex adapter
builds its own hostname — so an in-process test supplies a `provider.Provider`
whose `BuildRequest` targets the mock instead. See `mockProvider` in
`harness_test.go`.

Docker — note the context is the **parent** directory:

```bash
docker build -f mockupstream/Dockerfile -t la-mockupstream backend/llmguard
docker run --rm -p 8090:8090 la-mockupstream -error-rate 0.25 -latency 150ms
```

## Recipes

```bash
# Every request fails with 503 + Retry-After: 3
curl -i -X POST 'localhost:8090/v1/chat/completions?error_rate=1.0&retry_after=3' \
  -H 'Content-Type: application/json' -d '{"messages":[]}'

# Drive the circuit breaker: total outage for 30s
curl -X POST 'localhost:8090/_mock/outage?duration=30s'

# Slow streaming, to make time-to-first-token measurable
curl -N -X POST 'localhost:8090/v1/chat/completions?chunk_delay=50ms' \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"hi"}],"stream":true}'
```
