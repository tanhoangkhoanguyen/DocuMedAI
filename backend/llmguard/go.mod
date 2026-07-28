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
	github.com/redis/go-redis/v9 v9.7.0 // Redis-backed rate limit + dedup state
	github.com/sony/gobreaker v1.0.0 // circuit breaker around upstream
	golang.org/x/sync v0.10.0 // singleflight for same-process dedup
)

require (
	github.com/beorn7/perks v1.0.1 // indirect
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/dgryski/go-rendezvous v0.0.0-20200823014737-9f7001d12a5f // indirect
	github.com/klauspost/compress v1.17.9 // indirect
	github.com/kylelemons/godebug v1.1.0 // indirect
	github.com/munnerz/goautoneg v0.0.0-20191010083416-a7dc8b61c822 // indirect
	github.com/prometheus/client_model v0.6.1 // indirect
	github.com/prometheus/common v0.55.0 // indirect
	github.com/prometheus/procfs v0.15.1 // indirect
	golang.org/x/sys v0.22.0 // indirect
	google.golang.org/protobuf v1.34.2 // indirect
)
