package gateway

// OpenTelemetry tracing setup.
//
// Why traces when there are already Prometheus metrics: the metrics count what
// happened (1000 requests, 50 errors, p99 8s) but cannot say why ONE request took
// 8s. A gateway sits in the request path, so the question it is asked is almost
// always "which stage was slow" — retry loop, quota wait, or the upstream itself.
// That is a per-request timing tree, which is what a trace is.
//
// Disabled is the default and it is disabled COMPLETELY: with no endpoint
// configured this installs nothing, and the global tracer stays the no-op the
// OpenTelemetry API ships with. Two measured notes behind that choice:
//
//   - The no-op path is not free but is irrelevant: ~34ns and one allocation per
//     span, against a request that spends seconds inside an LLM call. Guarding
//     every span with an `if enabled` would save ~32ns at the cost of a branch at
//     every call site, so there is no such flag.
//   - Installing the SDK with a zero sample ratio is NOT the cheap way to switch
//     tracing off. The SDK builds a recording span before the sampler drops it,
//     which measured ~17x the no-op cost. Leaving TraceEndpoint empty is the
//     switch.

import (
	"context"
	"fmt"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"
	"go.opentelemetry.io/otel/trace"
)

// setupTracing installs a tracer provider and returns its shutdown function.
//
// An empty cfg.TraceEndpoint is the disabled case and is not an error: the
// returned shutdown is a working no-op, so main needs no conditional around it.
func setupTracing(ctx context.Context, cfg Config) (func(context.Context) error, error) {
	if cfg.TraceEndpoint == "" {
		return func(context.Context) error { return nil }, nil
	}

	// No endpoint option is passed on purpose. The exporter reads
	// OTEL_EXPORTER_OTLP_ENDPOINT itself (appending /v1/traces) and
	// OTEL_EXPORTER_OTLP_TRACES_ENDPOINT verbatim; supplying WithEndpointURL here
	// would override whichever of the two the operator actually set, and silently
	// disagree with the value this function just tested for emptiness.
	exp, err := otlptracehttp.New(ctx)
	if err != nil {
		return nil, fmt.Errorf("otlp trace exporter: %w", err)
	}

	// schema.Empty rather than resource.Default(): the default carries the SDK's
	// own schema URL, and merging it with a different semconv version is an error
	// that surfaces as a confusing "conflicting Schema URL" at startup.
	res, err := resource.Merge(
		resource.Empty(),
		resource.NewWithAttributes(
			semconv.SchemaURL,
			semconv.ServiceName(cfg.TraceServiceName),
		),
	)
	if err != nil {
		return nil, fmt.Errorf("trace resource: %w", err)
	}

	tp := sdktrace.NewTracerProvider(
		// Batched, not synchronous: an export must never sit on a request's
		// critical path, and a collector that has gone away must not turn into
		// gateway latency.
		sdktrace.WithBatcher(exp),
		sdktrace.WithResource(res),
		// ParentBased wraps the ratio so an inbound sampled decision is honored.
		// Without it a request the caller chose to trace would be re-diced here
		// and could vanish from the middle of a trace that already exists.
		sdktrace.WithSampler(sdktrace.ParentBased(
			sdktrace.TraceIDRatioBased(cfg.TraceSampleRatio),
		)),
	)
	otel.SetTracerProvider(tp)

	// Without an explicit propagator the global default is a no-op that extracts
	// and injects nothing — inbound traceparent headers would be ignored and every
	// request would start a fresh, disconnected trace. That would quietly discard
	// the main reason to trace a gateway at all: seeing the caller and the upstream
	// call in ONE trace. Baggage rides along because it is the other half of the
	// W3C pair and costs nothing when unused.
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	))

	return tp.Shutdown, nil
}

// Attribute keys.
//
// Named constants for the same reason metrics.go's abort reasons are: an
// attribute key is an interface. Once a dashboard or a saved query filters on
// one, renaming it returns no rows rather than failing, so the keys belong in one
// reviewable list instead of scattered through the pipeline as string literals.
const (
	attrProvider = attribute.Key("llmguard.provider")
	attrModel    = attribute.Key("llmguard.model")

	// attrRefusedBy names WHICH protection refused a request.
	//
	// This is the one thing the Prometheus metrics cannot answer. LLMGuard refuses
	// in five distinct ways and two PAIRS share a status code: admission control
	// and the rate limiter both return 429, and the local and cross-replica
	// breakers both return 503. A 429 that means "this caller is over quota" and a
	// 429 that means "the gateway is out of capacity" call for opposite responses
	// — one is the client's problem, the other is the operator's — and on the wire
	// they are indistinguishable.
	//
	// llmguard_shed_total vs llmguard_rate_limited_total separate the first pair in
	// aggregate, but a counter cannot tell you which ONE request in a trace was
	// refused and why. That is what this attribute is for.
	attrRefusedBy = attribute.Key("llmguard.refused_by")

	// attrCoalesced records that a request's flight had more than one caller.
	//
	// Deliberately NOT named "dedup.hit". singleflight reports shared=true to the
	// flight LEADER as well as its followers, so "hit" would claim the leader
	// reused someone else's response when in fact it made the upstream call.
	// "coalesced" is true of every caller in the flight, which is what the flag
	// actually means.
	attrCoalesced = attribute.Key("llmguard.dedup.coalesced")
)

// Refusal reasons for attrRefusedBy. One per way a request can be turned away.
const (
	// The in-flight ceiling was full — an operator capacity problem, 429.
	refusedByAdmission = "admission"
	// The caller's token bucket was empty — a client quota problem, also 429.
	refusedByQuota = "quota"
	// Another replica published this provider as down — 503, no local evidence.
	refusedByBreakerRemote = "breaker_remote"
	// This replica's own breaker is open, or the transport failed outright — 503.
	refusedByBreakerLocal = "breaker_local"
	// The vendor itself refused; its status and message are passed through.
	refusedByUpstream = "upstream"
)

// markRefused records on the request's span which protection turned it away.
//
// The span comes from the request context, so this is a no-op when tracing is off
// or when the route is unwrapped: SpanFromContext returns a non-recording span
// rather than nil, and SetAttributes on it does nothing. That is why there is no
// enabled check here or at any call site.
func markRefused(ctx context.Context, reason, provName, model string) {
	span := trace.SpanFromContext(ctx)
	if !span.IsRecording() {
		return
	}
	span.SetAttributes(
		attrRefusedBy.String(reason),
		attrProvider.String(provName),
		attrModel.String(model),
	)
}
