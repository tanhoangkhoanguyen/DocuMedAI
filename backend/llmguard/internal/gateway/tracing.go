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
	"errors"
	"fmt"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"
	"go.opentelemetry.io/otel/trace"

	"documedai/llmguard/provider"
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

// tracerName labels the spans this package emits with their origin, which is how
// a backend groups spans by instrumentation rather than only by service.
const tracerName = "documedai/llmguard/internal/gateway"

// tracer resolves the GLOBAL provider on every call rather than caching one.
//
// Caching would capture whichever provider was installed when this package was
// first touched — the no-op, since setupTracing runs later in main — and then
// ignore setupTracing entirely. It would also defeat the tests, which swap the
// global per test to record spans.
func tracer() trace.Tracer { return otel.Tracer(tracerName) }

// spanAttempt names the per-attempt child span.
//
// One span per attempt rather than one for the whole retry loop, because the loop
// total answers "was it slow" while the attempts answer "why": four siblings, three
// of them failing, is a different picture from one slow call, and the two are
// indistinguishable in a single span.
const spanAttempt = "upstream.attempt"

// Attempt-span attributes.
const (
	// attrAttempt is the 0-based attempt number: 0 is the first try, not a retry.
	// It matches doWithRetry's own loop counter, and deliberately not
	// llmguard_retries_total, which counts only attempts after the first.
	attrAttempt = attribute.Key("llmguard.retry.attempt")

	// attrStatus is the upstream's HTTP status for this attempt.
	//
	// Per-attempt rather than per-request, which is the point: a request that ends
	// 200 can still have burned a 503 and a 429 on the way, and only the final
	// status reaches llmguard_requests_total.
	attrStatus = attribute.Key("llmguard.upstream.status")
)

// startAttempt opens the per-attempt child span.
//
// Returns the derived context so anything below inherits the attempt as its
// parent rather than the root — that is what makes the tree show retries as
// siblings instead of a flat list.
func startAttempt(
	ctx context.Context, provName, model string, attempt int,
) (context.Context, trace.Span) {
	return tracer().Start(ctx, spanAttempt, trace.WithAttributes(
		attrProvider.String(provName),
		attrModel.String(model),
		attrAttempt.Int(attempt),
	))
}

// endAttempt closes an attempt span, recording its outcome.
//
// An upstream error carries the vendor's status and is recorded as an error; a
// result without one is the success path. Both set attrStatus, so an attempt is
// never in the tree without saying what happened to it.
func endAttempt(span trace.Span, res *upstreamResult, err error) {
	defer span.End()
	if !span.IsRecording() {
		return
	}
	switch {
	case err != nil:
		var ue *provider.UpstreamError
		if errors.As(err, &ue) {
			span.SetAttributes(attrStatus.Int(ue.Status))
		}
		// RecordError keeps the message as a span event; SetStatus is what makes
		// the span render as failed. Both, because a trace UI reads them
		// differently — one is detail, the other is the red marker.
		span.RecordError(err)
		span.SetStatus(codes.Error, err.Error())
	case res != nil:
		span.SetAttributes(attrStatus.Int(res.status))
	}
}

// spanStream names the child span covering one SSE stream.
//
// ONE span for the whole stream, deliberately — not one per frame. A frame span
// would be the natural symmetry with upstream.attempt, and it is a non-starter:
// streams here reach tens of thousands of frames (the stalled-reader test drives
// 50k tokens), and at ~780 bytes per sampled span that is tens of megabytes of
// span data for a single request. That is the same exhaustion vector
// maxUpstreamBody exists to prevent, and it would overrun the batch queue and be
// unreadable in a trace UI besides. What the frames are actually asked is "when
// did the first one arrive" and "why did they stop", and both are answerable
// without a span each.
const spanStream = "stream"

// Stream-span attributes and events.
const (
	// attrStreamFrames is how many chunks were written to the client.
	//
	// Together with the span's own duration it separates "a long answer" from "a
	// stream that hung": the same 90 seconds means something different at 2000
	// frames than at 3.
	attrStreamFrames = attribute.Key("llmguard.stream.frames")

	// attrAbortReason names which deadline cut the stream, reusing the vocabulary
	// of llmguard_stream_aborts_total (upstream_idle | write_idle | absolute_max).
	//
	// Needed on the span for the reason the metric is needed at all: the header
	// left with the first frame, so an aborted stream already recorded a 2xx and is
	// otherwise invisible. The attribute is what makes ONE such request findable
	// rather than only countable.
	attrAbortReason = attribute.Key("llmguard.stream.abort_reason")

	// eventFirstFrame marks the first chunk reaching the client — time to first
	// token.
	//
	// This is the number an LLM gateway is judged on and it exists nowhere else
	// here: request_duration_seconds measures the whole stream, which is dominated
	// by how LONG the answer is rather than by how fast the gateway and provider
	// started producing it. A span event is the cheapest possible way to record it
	// (no new span, no new metric) and it lands on the timeline exactly where a
	// reader looks for it.
	eventFirstFrame = "first_frame"

	// eventUpstreamHeaders marks the upstream's 2xx arriving, i.e. the boundary
	// between connect/negotiate and generation. The gap from here to
	// eventFirstFrame is the provider thinking; before it is ours.
	eventUpstreamHeaders = "upstream_headers"
)

// startStream opens the stream span as a child of the request.
func startStream(
	ctx context.Context, provName, model string,
) (context.Context, trace.Span) {
	return tracer().Start(ctx, spanStream, trace.WithAttributes(
		attrProvider.String(provName),
		attrModel.String(model),
	))
}

// endStream closes a stream span with its outcome.
//
// abortReason is the value streamAbortReason already computed for the metric —
// passed in rather than recomputed, so the span and the counter can never
// disagree about why a stream ended. An empty reason with a non-nil error is an
// ordinary upstream failure rather than a deadline.
func endStream(span trace.Span, frames int, abortReason string, err error) {
	defer span.End()
	if !span.IsRecording() {
		return
	}
	span.SetAttributes(attrStreamFrames.Int(frames))
	if abortReason != "" {
		span.SetAttributes(attrAbortReason.String(abortReason))
	}
	if err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, err.Error())
	}
}
