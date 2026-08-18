package gateway

// Tests for tracing setup.
//
// These state INTENT rather than characterizing existing behavior — the same
// exception admission_test.go and proxy_streaming_deadline_test.go take. Tracing
// is new, so there is no prior conduct to preserve, and the contract worth pinning
// is the one that is easy to break silently: with no endpoint configured the
// gateway must behave exactly as it did before tracing existed.
//
// None of these may call t.Parallel(): they read and replace the PROCESS-WIDE
// tracer provider, so two running concurrently would observe each other's writes.
// Same constraint TestRealDefaultsMatchLoadConfig already lives with via t.Setenv.

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"

	"documedai/llmguard/internal/testutil"
	"documedai/llmguard/mockupstream"
)

// TestTracingDisabledInstallsNothing pins the disabled contract at the setup layer:
// an empty endpoint must not install a provider, and must not report an error.
//
// The global provider is captured and compared by identity. A future change that
// "helpfully" installs an SDK with a zero sample ratio when tracing is off would
// pass a status-code test but fail here — and it matters, because that path was
// measured at ~17x the no-op cost per span.
func TestTracingDisabledInstallsNothing(t *testing.T) {
	before := otel.GetTracerProvider()

	cfg := realDefaults()
	cfg.TraceEndpoint = "" // the switch

	shutdown, err := setupTracing(context.Background(), cfg)
	if err != nil {
		t.Fatalf("setupTracing with no endpoint returned an error: %v", err)
	}
	if shutdown == nil {
		t.Fatal("shutdown is nil; main calls it unconditionally and would panic")
	}
	if after := otel.GetTracerProvider(); after != before {
		t.Errorf("the global tracer provider was replaced while tracing is disabled\n"+
			"before=%T after=%T — disabled must mean nothing is installed", before, after)
	}
	// Must be safe to call, and must not block: main defers it on every exit path.
	if err := shutdown(context.Background()); err != nil {
		t.Errorf("no-op shutdown returned an error: %v", err)
	}
}

// TestTracingDisabledServesNormally is ROADMAP 3.1's second acceptance criterion:
// with tracing off the gateway serves a request normally, with no errors.
//
// Worth its own test because the failure it guards against is a panic, not a wrong
// number: any span created against a nil tracer, or an attribute set on a nil span,
// takes the whole request down. Running the real pipeline under the default no-op
// provider is the only way to catch that.
func TestTracingDisabledServesNormally(t *testing.T) {
	cfg := realDefaults()
	cfg.TraceEndpoint = ""

	h := newHarness(t, cfg, mockupstream.Config{}, nil)

	rec := h.do(t, chatBody("gemini-2.5-flash", "hello", false), nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}
	if got := decodeChat(t, rec); len(got.Choices) == 0 {
		t.Error("response carried no choices")
	}
	if n := h.up.Hits(); n != 1 {
		t.Errorf("upstream calls = %d, want 1", n)
	}
}

// TestTracingEnabledInstallsAProvider is the other half of the switch: a non-empty
// endpoint must actually install an SDK provider.
//
// Paired with TestTracingDisabledInstallsNothing on purpose — together they pin
// both directions. A regression that installed nothing in BOTH cases would leave
// the disabled test passing and tracing quietly dead.
//
// No collector is needed. The exporter connects lazily, so construction succeeds
// against an endpoint where nothing listens; only an export attempt would fail, and
// that happens on the batcher's own goroutine.
func TestTracingEnabledInstallsAProvider(t *testing.T) {
	before := otel.GetTracerProvider()
	t.Cleanup(func() { otel.SetTracerProvider(before) })

	cfg := realDefaults()
	cfg.TraceEndpoint = "http://127.0.0.1:1" // reserved; nothing listens
	cfg.TraceServiceName = "llmguard-test"
	cfg.TraceSampleRatio = 1.0

	shutdown, err := setupTracing(context.Background(), cfg)
	if err != nil {
		t.Fatalf("setupTracing with an endpoint set: %v", err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = shutdown(ctx)
	})

	if otel.GetTracerProvider() == before {
		t.Error("the global tracer provider was not replaced; tracing is configured " +
			"but no SDK is installed, so nothing would ever be exported")
	}

	// The propagator matters as much as the provider: the global default extracts
	// nothing, which would silently break the cross-service traces that are the
	// main reason to trace a gateway.
	if fields := otel.GetTextMapPropagator().Fields(); len(fields) == 0 {
		t.Error("no propagator fields registered; an inbound traceparent would be " +
			"ignored and every request would start a disconnected trace")
	}
}

// recordSpans installs an SDK tracer provider that records into memory, plus the
// propagator setupTracing installs when tracing is enabled. Both are restored on
// cleanup.
//
// The GLOBAL provider is swapped rather than a tracer injected into Proxy, because
// that is how production wires it: the pipeline reads its span off the request
// context, which otelhttp populated from the global. Injecting a tracer would test
// a seam the shipped code does not have.
//
// AlwaysSample deliberately: a ratio would make every assertion below flaky by
// construction.
func recordSpans(t *testing.T) *tracetest.SpanRecorder {
	t.Helper()
	sr := tracetest.NewSpanRecorder()
	tp := sdktrace.NewTracerProvider(
		sdktrace.WithSampler(sdktrace.AlwaysSample()),
		sdktrace.WithSpanProcessor(sr),
	)
	prevTP, prevProp := otel.GetTracerProvider(), otel.GetTextMapPropagator()
	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{}, propagation.Baggage{}))
	t.Cleanup(func() {
		otel.SetTracerProvider(prevTP)
		otel.SetTextMapPropagator(prevProp)
		_ = tp.Shutdown(context.Background())
	})
	return sr
}

// tracedRequest drives one request through otelhttp + the proxy, the way main.go
// wires the completions route, and returns the recorded root span.
//
// The wrapping is what creates the span the pipeline decorates. A test calling
// h.do directly would decorate a non-recording span and pass no matter what the
// pipeline did, so it has to go through the handler production actually serves.
func tracedRequest(
	t *testing.T, h *harness, sr *tracetest.SpanRecorder,
	body string, headers map[string]string,
) (*httptest.ResponseRecorder, sdktrace.ReadOnlySpan) {
	t.Helper()

	wrapped := otelhttp.NewHandler(h.proxy, "POST /v1/chat/completions")
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		r.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	wrapped.ServeHTTP(rec, r)

	ended := sr.Ended()
	if len(ended) == 0 {
		t.Fatal("no spans recorded; otelhttp started none, so nothing the pipeline " +
			"sets could be observed")
	}
	// The root is the span with no local parent. Selecting it this way rather than
	// taking ended[0] keeps these tests correct once child spans exist.
	for _, s := range ended {
		if !s.Parent().IsValid() || s.Parent().IsRemote() {
			return rec, s
		}
	}
	return rec, ended[len(ended)-1]
}

// attrString reads a string attribute off a span, reporting whether it was set.
func attrString(s sdktrace.ReadOnlySpan, key attribute.Key) (string, bool) {
	for _, kv := range s.Attributes() {
		if kv.Key == key {
			return kv.Value.AsString(), true
		}
	}
	return "", false
}

// attrBool reads a bool attribute off a span, reporting whether it was set.
func attrBool(s sdktrace.ReadOnlySpan, key attribute.Key) (bool, bool) {
	for _, kv := range s.Attributes() {
		if kv.Key == key {
			return kv.Value.AsBool(), true
		}
	}
	return false, false
}

// A shed request is attributed to admission control, not to the rate limiter.
//
// This is the pair the wire cannot separate: both return 429. One request holds
// the single slot while a second arrives and is shed.
func TestRefusedByAdmissionIsRecorded(t *testing.T) {
	sr := recordSpans(t)

	cfg := realDefaults()
	cfg.MaxInFlight = 1

	mcfg := mockupstream.DefaultConfig()
	// Long enough that the first request still holds the slot when the second
	// arrives, short enough not to slow the suite.
	mcfg.Latency = 400 * time.Millisecond
	h := newHarness(t, cfg, mcfg, nil)

	// Occupy the only slot.
	occupied := make(chan struct{})
	go func() {
		defer close(occupied)
		h.do(t, chatBody("gemini-2.5-flash", "hold the slot", false), nil)
	}()
	testutil.RequireEventually(t, 2*time.Second, 5*time.Millisecond, func() bool {
		return testutil.CounterValue(t, h.metrics.inFlight) == 1
	}, "the first request never took the slot")

	rec, span := tracedRequest(t, h, sr, chatBody("gemini-2.5-flash", "shed me", false), nil)
	<-occupied

	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want 429 (shed)\nbody: %s", rec.Code, rec.Body.String())
	}
	got, ok := attrString(span, attrRefusedBy)
	if !ok {
		t.Fatalf("%s is not set on the span; a shed 429 is indistinguishable from a "+
			"quota 429 without it", attrRefusedBy)
	}
	if got != refusedByAdmission {
		t.Errorf("%s = %q, want %q", attrRefusedBy, got, refusedByAdmission)
	}
}

// A quota refusal is attributed to the rate limiter, not to admission control.
//
// The other half of the 429 pair. Needs a real Redis: with Redis unreachable the
// limiter fails OPEN and never refuses, so this path is unreachable offline.
func TestRefusedByQuotaIsRecorded(t *testing.T) {
	rdb := testutil.RequireRedis(t)
	sr := recordSpans(t)

	cfg := realDefaults()
	cfg.RateLimitRPM = 60
	cfg.RateLimitBurst = 1
	cfg.RateWaitMax = 150 * time.Millisecond

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 3
	h := newHarness(t, cfg, mcfg, newRateLimiter(rdb, cfg.RateLimitRPM, cfg.RateLimitBurst))

	headers := map[string]string{"Authorization": "Bearer sk-test-" + t.Name()}
	body := chatBody("gemini-2.5-flash", "quota", false)

	// Spend the single token.
	if first := h.do(t, body, headers); first.Code != http.StatusOK {
		t.Fatalf("first request = %d, want 200\nbody: %s", first.Code, first.Body.String())
	}

	rec, span := tracedRequest(t, h, sr, body, headers)
	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want 429 (quota)\nbody: %s", rec.Code, rec.Body.String())
	}
	got, ok := attrString(span, attrRefusedBy)
	if !ok {
		t.Fatalf("%s is not set on the span", attrRefusedBy)
	}
	if got != refusedByQuota {
		t.Errorf("%s = %q, want %q — a quota refusal must not be reported as a "+
			"capacity shed; they call for opposite responses",
			attrRefusedBy, got, refusedByQuota)
	}
}

// A vendor refusal is attributed upstream, not to a local guard.
//
// The mock returns a non-retryable 400, so the retry loop bails immediately and
// the vendor's own status travels downstream.
func TestRefusedByUpstreamIsRecorded(t *testing.T) {
	sr := recordSpans(t)

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusBadRequest
	h := newHarness(t, realDefaults(), mcfg, nil)

	rec, span := tracedRequest(t, h, sr, chatBody("gemini-2.5-flash", "bad", false), nil)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400 (vendor passthrough)\nbody: %s",
			rec.Code, rec.Body.String())
	}
	got, ok := attrString(span, attrRefusedBy)
	if !ok {
		t.Fatalf("%s is not set on the span", attrRefusedBy)
	}
	if got != refusedByUpstream {
		t.Errorf("%s = %q, want %q", attrRefusedBy, got, refusedByUpstream)
	}
}

// A successful request records dedup.coalesced=false and NO refusal.
//
// The negative case is what makes the refusal attribute meaningful when present.
// And a coalesced flag written only when true cannot be told apart from "not
// instrumented", which is why it is asserted present-and-false here.
func TestSuccessRecordsNoRefusalAndSoloFlight(t *testing.T) {
	sr := recordSpans(t)

	h := newHarness(t, realDefaults(), mockupstream.Config{}, nil)
	rec, span := tracedRequest(t, h, sr, chatBody("gemini-2.5-flash", "hello", false), nil)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}
	if got, ok := attrString(span, attrRefusedBy); ok {
		t.Errorf("%s = %q on a successful request; it must appear only on a refusal",
			attrRefusedBy, got)
	}
	coalesced, ok := attrBool(span, attrCoalesced)
	if !ok {
		t.Fatalf("%s is not set; it is recorded on every request so that absent means "+
			"'not instrumented' rather than 'false'", attrCoalesced)
	}
	if coalesced {
		t.Errorf("%s = true for a single request with no concurrent twin", attrCoalesced)
	}
}

// A tripped LOCAL breaker is attributed to breaker_local, not breaker_remote.
//
// The second indistinguishable pair: both return 503 with the same message. The
// difference is whose evidence was acted on — this replica's own failures, or a
// flag another replica published — and that decides whether an operator looks at
// one replica or at the provider.
//
// The cross-replica half needs Redis and a second replica's flag, which
// breakershare_test.go already covers; this test pins the local half, and the
// distinct constant is what keeps them from collapsing into one reading.
func TestRefusedByBreakerLocalIsRecorded(t *testing.T) {
	sr := recordSpans(t)

	cfg := realDefaults()
	// The breaker trips on failure ratio, not on delay; zeroing the backoff keeps
	// twelve failing requests from spending seconds asleep.
	cfg.RetryBaseDly = 0
	cfg.RetryMaxDly = 0

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	// 500 rather than 400: a retryable status is what counts as a breaker failure.
	// A 400 would return early as a vendor refusal and never trip anything.
	mcfg.ErrorStatus = http.StatusInternalServerError
	h := newHarness(t, cfg, mcfg, nil)

	// CircuitMinReqs=10 at a 0.6 failure ratio, so twelve all-failing requests trip
	// it with margin.
	for i := 0; i < 12; i++ {
		h.do(t, chatBody("gemini-2.5-flash", "trip the breaker", false), nil)
	}

	rec, span := tracedRequest(t, h, sr, chatBody("gemini-2.5-flash", "after trip", false), nil)
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503 (breaker open)\nbody: %s", rec.Code, rec.Body.String())
	}
	got, ok := attrString(span, attrRefusedBy)
	if !ok {
		t.Fatalf("%s is not set on the span; a local breaker 503 is indistinguishable "+
			"from a cross-replica one without it", attrRefusedBy)
	}
	if got != refusedByBreakerLocal {
		t.Errorf("%s = %q, want %q", attrRefusedBy, got, refusedByBreakerLocal)
	}
}
