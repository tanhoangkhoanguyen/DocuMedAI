// LLMGuard — an OpenAI-compatible LLM gateway. Clients speak the OpenAI wire
// format; internally a provider adapter translates to the vendor's native API
// (Vertex AI Gemini today). It adds rate limiting, retry/backoff, circuit
// breaking, in-flight de-duplication and Prometheus metrics so a burst of
// chat-completion calls never overwhelms the upstream (429/5xx).
//
// Clients only change `base_url` to point here; credentials stay in LLMGuard.
module documedai/llmguard

go 1.23

require (
	github.com/prometheus/client_golang v1.20.5 // Prometheus /metrics
	github.com/prometheus/client_model v0.6.1 // reading histograms back in tests
	github.com/redis/go-redis/v9 v9.7.0 // Redis-backed rate limit + dedup state
	github.com/sony/gobreaker v1.0.0 // circuit breaker around upstream
	golang.org/x/oauth2 v0.24.0 // ADC / OAuth2 tokens for Vertex AI
	golang.org/x/sync v0.10.0 // singleflight for same-process dedup
)

require (
	cloud.google.com/go/compute/metadata v0.3.0 // indirect
	github.com/beorn7/perks v1.0.1 // indirect
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/dgryski/go-rendezvous v0.0.0-20200823014737-9f7001d12a5f // indirect
	github.com/klauspost/compress v1.17.9 // indirect
	github.com/kylelemons/godebug v1.1.0 // indirect
	github.com/munnerz/goautoneg v0.0.0-20191010083416-a7dc8b61c822 // indirect
	github.com/prometheus/common v0.55.0 // indirect
	github.com/prometheus/procfs v0.15.1 // indirect
	golang.org/x/sys v0.22.0 // indirect
	google.golang.org/protobuf v1.34.2 // indirect
)
