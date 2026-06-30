package main

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

func newMetrics() *Metrics {
	return &Metrics{
		requests: promauto.NewCounterVec(prometheus.CounterOpts{
			Name: "llmproxy_requests_total",
			Help: "Total proxied requests by model and HTTP status.",
		}, []string{"model", "status"}),
		latency: promauto.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "llmproxy_request_duration_seconds",
			Help:    "End-to-end request latency by model.",
			Buckets: []float64{0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 40, 80},
		}, []string{"model"}),
		retries: promauto.NewCounterVec(prometheus.CounterOpts{
			Name: "llmproxy_retries_total",
			Help: "Upstream retry attempts by model.",
		}, []string{"model"}),
		rateLimited: promauto.NewCounterVec(prometheus.CounterOpts{
			Name: "llmproxy_rate_limited_total",
			Help: "Requests rejected by the token bucket by model.",
		}, []string{"model"}),
		dedupHits: promauto.NewCounter(prometheus.CounterOpts{
			Name: "llmproxy_dedup_hits_total",
			Help: "Requests served by sharing an in-flight identical call.",
		}),
		circuitState: promauto.NewGauge(prometheus.GaugeOpts{
			Name: "llmproxy_circuit_state",
			Help: "Circuit breaker state: 0=closed, 1=half-open, 2=open.",
		}),
		tokensUsed: promauto.NewCounterVec(prometheus.CounterOpts{
			Name: "llmproxy_tokens_total",
			Help: "Tokens reported by upstream usage, by model and kind.",
		}, []string{"model", "kind"}),
	}
}
