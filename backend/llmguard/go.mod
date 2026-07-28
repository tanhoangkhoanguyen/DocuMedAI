// LLMGuard — an OpenAI-compatible gateway that sits between the DocuMedAI
// Python backend (LangGraph / CrewAI) and the LLM provider (Gemini, via its
// OpenAI-compatible endpoint). It adds rate limiting, retry/backoff, circuit
// breaking, in-flight de-duplication and Prometheus metrics so a burst of
// chat-completion calls never overwhelms the upstream (429/5xx).
//
// Clients talk to LLMGuard using the OpenAI wire format: they only change
// `base_url` to point here. We inject the real upstream key on the way out.
module documedai/llmguard

go 1.23

require (
	github.com/prometheus/client_golang v1.20.5 // Prometheus /metrics
	github.com/redis/go-redis/v9 v9.7.0          // Redis-backed rate limit + dedup state
	github.com/sony/gobreaker v1.0.0             // circuit breaker around upstream
	golang.org/x/sync v0.10.0                    // singleflight for same-process dedup
)
