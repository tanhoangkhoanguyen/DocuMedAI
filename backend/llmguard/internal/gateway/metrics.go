package gateway

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// providerUnknown labels a request that failed before its provider was
// resolved — a bad method, an unreadable body, malformed JSON, a missing model,
// or a model no adapter claims.
//
// Those requests still cost the caller a response and still belong in
// requests_total, so they need SOME provider label. Dropping the observation
// instead would understate the error rate; reusing the configured default would
// blame an adapter that never ran. This mirrors the "unknown" already used for
// the model label on the same paths.
const providerUnknown = "unknown"

// Metrics bundles every Prometheus collector the proxy exposes at /metrics.
// Diagram box 7 (Observability & Metrics). Grafana/Loki wiring is left to a
// later, optional step — here we only EXPOSE the metrics.
//
// Every request-scoped vector leads with `provider`, so a dashboard can split
// traffic, latency, retries and spend per upstream. With two adapters serving
// overlapping models — Gemini via Vertex and via an openai-compat endpoint —
// a model-only label cannot answer "which upstream is degrading".
//
// NOTE: adding this label is a BREAKING change for any existing dashboard or
// recording rule that groups by model alone; those queries keep working but
// their series are now split per provider.
type Metrics struct {
	// requests counts every inbound proxy request, labelled by provider + model
	// + final HTTP status returned to the caller.
	requests *prometheus.CounterVec
	// latency is the end-to-end time the caller waited, per provider + model.
	latency *prometheus.HistogramVec
	// retries counts how many upstream retry attempts we burned, per provider + model.
	retries *prometheus.CounterVec
	// rateLimited counts requests rejected by the token bucket (429), per
	// provider + model. Shedding happens AFTER the provider is resolved, so this
	// one always carries a real provider name.
	rateLimited *prometheus.CounterVec
	// dedupHits counts requests that piggy-backed on an in-flight identical call.
	dedupHits prometheus.Counter
	// circuitState reflects each provider's breaker: 0=closed, 1=half-open,
	// 2=open. Labelled because the breakers are per provider — a single series
	// would let one upstream's outage overwrite every other upstream's reading.
	circuitState *prometheus.GaugeVec
	// tokensUsed sums prompt+completion tokens parsed from upstream `usage`.
	tokensUsed *prometheus.CounterVec // labels: provider, model, kind(prompt|completion)
}

// newMetrics registers the collectors on the DEFAULT registry, which is what
// /metrics exposes. It can only be called once per process — a second call
// panics on duplicate registration.
func newMetrics() *Metrics {
	return newMetricsWith(prometheus.DefaultRegisterer)
}

// newMetricsWith registers on a caller-supplied registry. Tests use a private
// one so each can build a Proxy without colliding on the global registry.
func newMetricsWith(reg prometheus.Registerer) *Metrics {
	auto := promauto.With(reg)
	return &Metrics{
		requests: auto.NewCounterVec(prometheus.CounterOpts{
			Name: "llmguard_requests_total",
			Help: "Total proxied requests by provider, model and HTTP status.",
		}, []string{"provider", "model", "status"}),
		latency: auto.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "llmguard_request_duration_seconds",
			Help:    "End-to-end request latency by provider and model.",
			Buckets: []float64{0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 40, 80},
		}, []string{"provider", "model"}),
		retries: auto.NewCounterVec(prometheus.CounterOpts{
			Name: "llmguard_retries_total",
			Help: "Upstream retry attempts by provider and model.",
		}, []string{"provider", "model"}),
		rateLimited: auto.NewCounterVec(prometheus.CounterOpts{
			Name: "llmguard_rate_limited_total",
			Help: "Requests rejected by the token bucket by provider and model.",
		}, []string{"provider", "model"}),
		dedupHits: auto.NewCounter(prometheus.CounterOpts{
			Name: "llmguard_dedup_hits_total",
			Help: "Requests served by sharing an in-flight identical call.",
		}),
		circuitState: auto.NewGaugeVec(prometheus.GaugeOpts{
			Name: "llmguard_circuit_state",
			Help: "Circuit breaker state by provider: 0=closed, 1=half-open, 2=open.",
		}, []string{"provider"}),
		tokensUsed: auto.NewCounterVec(prometheus.CounterOpts{
			Name: "llmguard_tokens_total",
			Help: "Tokens reported by upstream usage, by provider, model and kind.",
		}, []string{"provider", "model", "kind"}),
	}
}
