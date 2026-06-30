# llm-proxy

An **OpenAI-API-compatible** gateway that sits between the DocuMedAI Python
backend (LangGraph / CrewAI) and the LLM provider. Today the upstream is
**Gemini via its OpenAI-compatible endpoint**; "OpenAI" here refers to the wire
format, not the provider. It exists so a burst of `gemini-2.5-flash` calls from
the `/chatbot` route never overwhelms the upstream (429/5xx).

```
backend (ChatOpenAI / crewai.LLM, base_url=…)  →  la-llm-proxy :8081  →  generativelanguage.googleapis.com/v1beta/openai
                                                   ├ rate limit (Redis token bucket)
                                                   ├ retry + backoff (honors Retry-After)
                                                   ├ circuit breaker (fail fast on outage)
                                                   ├ in-flight dedup (singleflight)
                                                   └ Prometheus /metrics
```

Only `gemini-2.5-flash` chat completions are proxied. Embeddings
(`all-MiniLM-L6-v2`) and the reranker (`BAAI/bge-reranker-v2-m3`) run **locally**
in the backend and never reach this proxy.

The upstream is OpenAI-compatible, so switching providers is just a matter of
changing `OPENAI_UPSTREAM_BASE` + `UPSTREAM_API_KEY` — no code change.

## Endpoints

| Path | Purpose |
|------|---------|
| `POST /v1/chat/completions` (and any `/v1/*`) | Proxied upstream with the real key injected |
| `GET /healthz` | Liveness (used by the docker healthcheck via `-healthcheck`) |
| `GET /metrics` | Prometheus metrics |

## Configuration (env)

| Var | Default | Notes |
|-----|---------|-------|
| `UPSTREAM_API_KEY` | falls back to `OPENAI_API_KEY` | The **real** upstream key (your Gemini key); injected on the way out |
| `OPENAI_UPSTREAM_BASE` | `https://api.openai.com/v1` | Set to `https://generativelanguage.googleapis.com/v1beta/openai` for Gemini |
| `PROXY_PORT` | `8081` | |
| `REDIS_URL` | `redis://la-redis:6379/1` | DB 1 — separate from app cache (DB 0) |
| `RATE_LIMIT_RPM` / `RATE_LIMIT_BURST` / `RATE_WAIT_MAX` | `480` / `60` / `5s` | Token bucket |
| `RETRY_MAX` / `RETRY_BASE_DELAY` / `RETRY_MAX_DELAY` | `4` / `300ms` / `8s` | Backoff |
| `CIRCUIT_MIN_REQUESTS` / `CIRCUIT_FAIL_RATIO` / `CIRCUIT_OPEN_FOR` | `10` / `0.6` / `20s` | Breaker |
| `UPSTREAM_TIMEOUT` / `MAX_IDLE_CONNS` | `120s` / `100` | HTTP client |

The proxy strips a leading `/v1` from inbound paths before appending to
`OPENAI_UPSTREAM_BASE`, so the Gemini base must **not** end in `/v1`.

## How the backend points here

`backend/services/chatbot/llm_config.py::get_llm_base_url()` reads
`LLM_PROXY_BASE_URL` (set in docker-compose for `la-backend`) and is passed as
`base_url=` to every `ChatOpenAI(...)` and `crewai.LLM(...)`. Set
`LLM_PROXY_BASE_URL=""` to bypass the proxy and call the provider directly.

## Run / test locally

```bash
# Build + run via compose (preferred)
docker compose up -d --build la-llm-proxy

# Smoke test
curl localhost:8081/healthz
curl localhost:8081/metrics

# Standalone (needs a local Go toolchain + a Redis on REDIS_URL)
UPSTREAM_API_KEY=… \
OPENAI_UPSTREAM_BASE=https://generativelanguage.googleapis.com/v1beta/openai \
go run .
```

## Files

| File | Responsibility |
|------|----------------|
| `main.go` | Wiring, HTTP server, graceful shutdown, `-healthcheck` |
| `config.go` | Env-driven config + defaults |
| `proxy.go` | Request handling, passthrough, streaming, header/key injection, usage metrics |
| `ratelimit.go` | Redis token bucket (atomic Lua) |
| `retry.go` | Exponential backoff + jitter + Retry-After + circuit breaker |
| `dedup.go` | In-flight de-duplication (singleflight) |
| `metrics.go` | Prometheus collectors |

## Deferred (see plan)

- Cross-replica dedup via Redis marker (single replica today — extension point in `dedup.go`).
- Grafana/Loki dashboards (metrics are exposed; wiring a stack is optional).
- PostgreSQL billing/quota store.
