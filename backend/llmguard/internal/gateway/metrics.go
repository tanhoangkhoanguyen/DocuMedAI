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
// traffic and refusals per upstream. With two adapters serving
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
	// rateLimited counts requests rejected by the token bucket (429), per
	// provider + model. Shedding happens AFTER the provider is resolved, so this
	// one always carries a real provider name.
	rateLimited *prometheus.CounterVec
	// circuitState reflects each route's breaker: 0=closed, 1=half-open, 2=open.
	// Labelled by provider AND model because the breakers are per route — a model
	// that dies on one upstream must not read as that whole upstream being down.
	circuitState *prometheus.GaugeVec
	// streamAborts counts streams cut by a streaming deadline, by provider +
	// model + reason.
	//
	// Needed because an aborted stream is otherwise INVISIBLE here. The header
	// goes out with the first frame, so requests_total already recorded a 2xx, and
	// the failure reaches the client as an in-band SSE frame that no server-side
	// counter observes. Without this, an upstream stalling on every request looks
	// exactly like ordinary successful traffic.
	//
	// The reason label is what makes it actionable, because the causes live on
	// opposite sides of the gateway: upstream_idle is a provider going quiet
	// mid-stream, write_idle is clients that stopped reading. One counter for both
	// would report that streams are being cut without saying who to go fix.
	streamAborts *prometheus.CounterVec
	// inFlight is how many requests currently hold an admission slot.
	//
	// Unlabelled, unlike every other vector here: the semaphore it mirrors is one
	// process-wide pool, so splitting it per provider would produce numbers that
	// no single limit corresponds to — you could not compare any series against
	// MaxInFlight. This is the gauge to chart next to that ceiling, and the one
	// whose saturation predicts shedding before it starts.
	inFlight prometheus.Gauge
	// shed counts requests refused by admission control, by provider + model.
	//
	// Labelled where inFlight is not, because the actionable question about a
	// refusal is WHICH traffic got refused, while the actionable question about
	// occupancy is how full the single pool is.
	//
	// Distinct from rateLimited on purpose: both return 429, but they say
	// different things. rateLimited means the caller exceeded its quota — expected,
	// self-inflicted, per-key. shed means the gateway is out of capacity — every
	// caller is affected regardless of quota, and it is an operator problem. One
	// counter for both would hide a capacity incident inside normal throttling.
	shed *prometheus.CounterVec
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
		rateLimited: auto.NewCounterVec(prometheus.CounterOpts{
			Name: "llmguard_rate_limited_total",
			Help: "Requests rejected by the token bucket by provider and model.",
		}, []string{"provider", "model"}),
		circuitState: auto.NewGaugeVec(prometheus.GaugeOpts{
			Name: "llmguard_circuit_state",
			Help: "Circuit breaker state by route: 0=closed, 1=half-open, 2=open.",
		}, []string{"provider", "model"}),
		inFlight: auto.NewGauge(prometheus.GaugeOpts{
			Name: "llmguard_in_flight",
			Help: "Requests currently holding an admission slot (compare against MAX_IN_FLIGHT).",
		}),
		shed: auto.NewCounterVec(prometheus.CounterOpts{
			Name: "llmguard_shed_total",
			Help: "Requests refused by admission control because the gateway was at capacity.",
		}, []string{"provider", "model"}),
		streamAborts: auto.NewCounterVec(prometheus.CounterOpts{
			Name: "llmguard_stream_aborts_total",
			Help: "Streams cut by a streaming deadline, by reason (upstream_idle|write_idle).",
		}, []string{"provider", "model", "reason"}),
	}
}

// Abort reasons for streamAborts. Named constants because each is written in one
// place and asserted in another, and a typo in either would silently produce a
// second, permanently-zero series rather than a failure.
//
// These must cover EVERY way a stream can be cut, or the gap is invisible: an
// unlabelled abort still returns HTTP 200 (the header left with the first frame)
// and reports itself only as an in-band SSE frame, so it lands in no counter at
// all.
const (
	// The upstream went quiet between frames â€” a provider problem.
	abortUpstreamIdle = "upstream_idle"
	// The client stopped reading and writes backed up â€” a client problem.
	abortWriteIdle = "write_idle"
	// StreamAbsoluteMax elapsed.
	//
	// This one is a LLMGuard problem, not either endpoint's. The backstop only
	// fires once the two inactivity bounds above have failed to, so any value here
	// means a stream ran 30 minutes while looking active the whole way â€” either
	// the write deadline degraded to a no-op behind a ResponseWriter wrapper, or
	// an upstream is dribbling frames just fast enough to keep resetting the
	// watchdog. Both are bugs in the protection itself.
	//
	// Precisely because it should never fire, it is the reason that most needs a
	// name: left unlabelled it surfaces as an ordinary transport error and the
	// dashboard shows a few upstream failures rather than "the deadlines are not
	// working".
	abortAbsoluteMax = "absolute_max"
)
