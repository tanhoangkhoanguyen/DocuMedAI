package gateway

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// Metrics bundles every Prometheus collector the proxy exposes at /metrics.
// Diagram box 7 (Observability & Metrics). Grafana/Loki wiring is left to a
// later, optional step — here we only EXPOSE the metrics.
type Metrics struct {
	// requests counts every inbound proxy request, labelled by model + final
	// HTTP status returned to the caller.
	requests *prometheus.CounterVec
	// latency is the end-to-end time the caller waited, per model.
	latency *prometheus.HistogramVec
	// retries counts how many upstream retry attempts we burned, per model.
	retries *prometheus.CounterVec
	// rateLimited counts requests rejected by the token bucket (429), per model.
	rateLimited *prometheus.CounterVec
	// dedupHits counts requests that piggy-backed on an in-flight identical call.
	dedupHits prometheus.Counter
	// circuitState reflects the breaker: 0=closed, 1=half-open, 2=open.
	circuitState prometheus.Gauge
	// tokensUsed sums prompt+completion tokens parsed from upstream `usage`.
	tokensUsed *prometheus.CounterVec // labels: model, kind(prompt|completion)
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
			Help: "Total proxied requests by model and HTTP status.",
		}, []string{"model", "status"}),
		latency: auto.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "llmguard_request_duration_seconds",
			Help:    "End-to-end request latency by model.",
			Buckets: []float64{0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 40, 80},
		}, []string{"model"}),
		retries: auto.NewCounterVec(prometheus.CounterOpts{
			Name: "llmguard_retries_total",
			Help: "Upstream retry attempts by model.",
		}, []string{"model"}),
		rateLimited: auto.NewCounterVec(prometheus.CounterOpts{
			Name: "llmguard_rate_limited_total",
			Help: "Requests rejected by the token bucket by model.",
		}, []string{"model"}),
		dedupHits: auto.NewCounter(prometheus.CounterOpts{
			Name: "llmguard_dedup_hits_total",
			Help: "Requests served by sharing an in-flight identical call.",
		}),
		circuitState: auto.NewGauge(prometheus.GaugeOpts{
			Name: "llmguard_circuit_state",
			Help: "Circuit breaker state: 0=closed, 1=half-open, 2=open.",
		}),
		tokensUsed: auto.NewCounterVec(prometheus.CounterOpts{
			Name: "llmguard_tokens_total",
			Help: "Tokens reported by upstream usage, by model and kind.",
		}, []string{"model", "kind"}),
	}
}
