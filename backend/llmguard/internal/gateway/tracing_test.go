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
	"go.opentelemetry.io/otel/codes"
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

// rootSpanName is the operation name main.go gives the completions route. Shared
// by the handler and the lookup below so the two cannot drift apart.
const rootSpanName = "POST /v1/chat/completions"

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

	wrapped := otelhttp.NewHandler(h.proxy, rootSpanName)
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
	// Match on the operation NAME, and search backwards.
	//
	// "the span with no parent" looks like the natural rule and is wrong here: a
	// test may drive warm-up requests through h.do (unwrapped, so no root span
	// exists) and every child span those produce is parentless too. Picking the
	// first parentless span then returns a warm-up attempt and the assertions read
	// an empty span. Backwards because otelhttp's root ends last, after the
	// children it contains.
	for i := len(ended) - 1; i >= 0; i-- {
		if ended[i].Name() == rootSpanName {
			return rec, ended[i]
		}
	}
	t.Fatalf("no %q span was recorded among %d spans", rootSpanName, len(ended))
	return rec, nil
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

// A successful request records NO refusal.
//
// The negative case is what makes the refusal attribute meaningful when present:
// an attribute that appeared on every span would say nothing.
func TestSuccessRecordsNoRefusal(t *testing.T) {
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

// spansNamed returns the recorded spans with the given name, in end order.
func spansNamed(sr *tracetest.SpanRecorder, name string) []sdktrace.ReadOnlySpan {
	var out []sdktrace.ReadOnlySpan
	for _, s := range sr.Ended() {
		if s.Name() == name {
			out = append(out, s)
		}
	}
	return out
}

// attrInt reads an int64 attribute off a span, reporting whether it was set.
func attrInt(s sdktrace.ReadOnlySpan, key attribute.Key) (int64, bool) {
	for _, kv := range s.Attributes() {
		if kv.Key == key {
			return kv.Value.AsInt64(), true
		}
	}
	return 0, false
}

// A retrying request produces one child span per attempt, numbered from zero.
//
// This is the payoff of the whole commit and ROADMAP 3.2's second acceptance
// criterion. Without it a request that spent 8s retrying three times is
// indistinguishable from one slow upstream call — the single most common question
// asked of a gateway, and one no counter answers.
func TestRetryProducesOneSpanPerAttempt(t *testing.T) {
	sr := recordSpans(t)

	cfg := realDefaults()
	// Only the delay knobs are shortened; RetryMax stays at the production 4, so
	// the count asserted below is the real one.
	cfg.RetryBaseDly = 0
	cfg.RetryMaxDly = 0

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	// Retryable, so the loop runs to exhaustion instead of bailing on the first
	// non-retryable status.
	mcfg.ErrorStatus = http.StatusServiceUnavailable
	h := newHarness(t, cfg, mcfg, nil)

	rec, _ := tracedRequest(t, h, sr, chatBody("gemini-2.5-flash", "retry me", false), nil)
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503 after exhausting retries\nbody: %s",
			rec.Code, rec.Body.String())
	}

	attempts := spansNamed(sr, spanAttempt)
	if len(attempts) != cfg.RetryMax {
		t.Fatalf("%q spans = %d, want %d (RetryMax); one span per attempt is what "+
			"makes a retry storm visible", spanAttempt, len(attempts), cfg.RetryMax)
	}
	if n := h.up.Hits(); int(n) != cfg.RetryMax {
		t.Errorf("upstream calls = %d, want %d — the span count must track real "+
			"attempts, not be generated independently of them", n, cfg.RetryMax)
	}

	// Numbered 0..RetryMax-1, in order. A test that only counted spans would pass
	// with every attempt labelled 0.
	for i, s := range attempts {
		got, ok := attrInt(s, attrAttempt)
		if !ok {
			t.Errorf("attempt span %d has no %s", i, attrAttempt)
			continue
		}
		if got != int64(i) {
			t.Errorf("attempt span %d: %s = %d, want %d", i, attrAttempt, got, i)
		}
		if status, ok := attrInt(s, attrStatus); !ok {
			t.Errorf("attempt span %d has no %s; an attempt in the tree must say "+
				"what happened to it", i, attrStatus)
		} else if status != int64(http.StatusServiceUnavailable) {
			t.Errorf("attempt span %d: %s = %d, want 503", i, attrStatus, status)
		}
		if s.Status().Code != codes.Error {
			t.Errorf("attempt span %d: status code = %v, want Error — a failed "+
				"attempt that renders green hides the retry", i, s.Status().Code)
		}
	}
}

// The attempt spans hang off the request's root span.
//
// Parentage is asserted rather than just membership: spans that record but do not
// nest render as a flat list, which loses the "these four attempts belong to that
// one request" relationship that makes the tree readable.
func TestAttemptSpansAreChildrenOfTheRequest(t *testing.T) {
	sr := recordSpans(t)

	h := newHarness(t, realDefaults(), mockupstream.Config{}, nil)
	rec, root := tracedRequest(t, h, sr, chatBody("gemini-2.5-flash", "hello", false), nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}

	attempts := spansNamed(sr, spanAttempt)
	if len(attempts) != 1 {
		t.Fatalf("%q spans = %d, want 1 for a request that succeeded first try",
			spanAttempt, len(attempts))
	}
	attempt := attempts[0]

	if got, want := attempt.Parent().SpanID(), root.SpanContext().SpanID(); got != want {
		t.Errorf("attempt parent = %s, want the root span %s", got, want)
	}
	if got, want := attempt.SpanContext().TraceID(), root.SpanContext().TraceID(); got != want {
		t.Errorf("attempt trace = %s, want %s — a child in a different trace is "+
			"invisible from the request", got, want)
	}
	// A first-try success must still be numbered 0 and marked OK, or "attempt 0"
	// would only ever appear on failures.
	if got, ok := attrInt(attempt, attrAttempt); !ok || got != 0 {
		t.Errorf("%s = %d (set=%v), want 0", attrAttempt, got, ok)
	}
	if attempt.Status().Code == codes.Error {
		t.Error("a successful attempt is marked as an error")
	}
	if status, ok := attrInt(attempt, attrStatus); !ok || status != http.StatusOK {
		t.Errorf("%s = %d (set=%v), want 200", attrStatus, status, ok)
	}
}

// A non-retryable vendor status produces exactly ONE attempt span.
//
// The counterpart to the retry test: it pins that the spans track what the loop
// actually did. retry.go returns early on a 400 because every further attempt
// would fail identically, and four spans here would misreport burned quota that
// was never spent.
func TestNonRetryableStatusProducesOneAttemptSpan(t *testing.T) {
	sr := recordSpans(t)

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusBadRequest
	h := newHarness(t, realDefaults(), mcfg, nil)

	rec, _ := tracedRequest(t, h, sr, chatBody("gemini-2.5-flash", "bad", false), nil)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400\nbody: %s", rec.Code, rec.Body.String())
	}

	if attempts := spansNamed(sr, spanAttempt); len(attempts) != 1 {
		t.Errorf("%q spans = %d, want 1 — a non-retryable status must not appear "+
			"as a retry storm", spanAttempt, len(attempts))
	}
}

// hasEvent reports whether a span recorded an event with the given name, and how
// many times.
func countEvent(s sdktrace.ReadOnlySpan, name string) int {
	n := 0
	for _, e := range s.Events() {
		if e.Name == name {
			n++
		}
	}
	return n
}

// A healthy stream produces ONE stream span carrying the frame count, a
// time-to-first-token event, and no abort reason.
//
// first_frame is the point of the commit: request_duration_seconds measures the
// whole stream, which is dominated by how long the answer is rather than by how
// quickly anything started arriving. Nothing else in the gateway records TTFT.
func TestStreamSpanRecordsFramesAndFirstToken(t *testing.T) {
	sr := recordSpans(t)

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 5
	h := newHarness(t, realDefaults(), mcfg, nil)

	rec, root := tracedRequest(t, h, sr, chatBody("gemini-2.5-flash", "stream", true), nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}

	streams := spansNamed(sr, spanStream)
	if len(streams) != 1 {
		t.Fatalf("%q spans = %d, want exactly 1 — one span per stream, never one "+
			"per frame", spanStream, len(streams))
	}
	stream := streams[0]

	if got, want := stream.Parent().SpanID(), root.SpanContext().SpanID(); got != want {
		t.Errorf("stream parent = %s, want the root span %s", got, want)
	}

	frames, ok := attrInt(stream, attrStreamFrames)
	if !ok {
		t.Fatalf("%s is not set; without it the span's duration cannot distinguish "+
			"a long answer from a hung stream", attrStreamFrames)
	}
	if frames <= 0 {
		t.Errorf("%s = %d, want > 0 for a stream that delivered chunks",
			attrStreamFrames, frames)
	}

	// Exactly once. A stream emits thousands of writes, and an event per frame is
	// the per-frame span problem in another shape.
	if n := countEvent(stream, eventFirstFrame); n != 1 {
		t.Errorf("%q events = %d, want exactly 1 (time to first token)",
			eventFirstFrame, n)
	}
	if n := countEvent(stream, eventUpstreamHeaders); n != 1 {
		t.Errorf("%q events = %d, want exactly 1", eventUpstreamHeaders, n)
	}
	// Ordering is what makes the pair useful: headers marks the end of connecting,
	// first_frame the start of output, and the gap between them is the provider
	// thinking rather than anything the gateway did.
	var headersAt, firstAt time.Time
	for _, e := range stream.Events() {
		switch e.Name {
		case eventUpstreamHeaders:
			headersAt = e.Time
		case eventFirstFrame:
			firstAt = e.Time
		}
	}
	if !headersAt.IsZero() && !firstAt.IsZero() && firstAt.Before(headersAt) {
		t.Errorf("%q (%v) precedes %q (%v); a frame cannot arrive before the headers",
			eventFirstFrame, firstAt, eventUpstreamHeaders, headersAt)
	}

	if reason, ok := attrString(stream, attrAbortReason); ok {
		t.Errorf("%s = %q on a healthy stream; it must appear only when a deadline "+
			"cut the stream", attrAbortReason, reason)
	}
	if stream.Status().Code == codes.Error {
		t.Error("a completed stream is marked as an error")
	}
}

// An aborted stream records WHY on its span, matching the counter's vocabulary.
//
// This is the case the span is most needed for: the header left with the first
// frame, so requests_total already recorded a 2xx and the request looks
// successful. Without the attribute an aborted stream is countable in aggregate
// but not findable as one request.
//
// Driven through mockupstream's StallAfter, which sends N frames and then goes
// silent without closing — the slow-loris shape the inter-frame watchdog exists
// for.
func TestAbortedStreamRecordsReasonOnSpan(t *testing.T) {
	sr := recordSpans(t)

	cfg := realDefaults()
	// Short enough to keep the test quick; the watchdog resets per frame, so this
	// only fires once upstream actually goes quiet.
	cfg.StreamIdleTimeout = 200 * time.Millisecond

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 50
	mcfg.StallAfter = 2 // two frames, then silence
	h := newHarness(t, cfg, mcfg, nil)

	rec, _ := tracedRequest(t, h, sr, chatBody("gemini-2.5-flash", "stall", true), nil)
	// The header went out with the first frame, so the status is already 200 —
	// which is exactly why the counter and this attribute exist.
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 (header left with the first frame)", rec.Code)
	}

	streams := spansNamed(sr, spanStream)
	if len(streams) != 1 {
		t.Fatalf("%q spans = %d, want 1", spanStream, len(streams))
	}
	stream := streams[0]

	got, ok := attrString(stream, attrAbortReason)
	if !ok {
		t.Fatalf("%s is not set on an aborted stream; the request already reported "+
			"HTTP 200, so nothing else marks it as cut", attrAbortReason)
	}
	if got != abortUpstreamIdle {
		t.Errorf("%s = %q, want %q", attrAbortReason, got, abortUpstreamIdle)
	}
	// Same vocabulary as llmguard_stream_aborts_total, and the same value: the span
	// and the counter are fed from one classification so they cannot disagree.
	if n := testutil.LabeledCounterValue(t, h.metrics.streamAborts,
		modelLabels("gemini-2.5-flash", abortUpstreamIdle)...); n != 1 {
		t.Errorf("stream_aborts_total{reason=%q} = %v, want 1", abortUpstreamIdle, n)
	}
	if stream.Status().Code != codes.Error {
		t.Errorf("status code = %v, want Error for an aborted stream", stream.Status().Code)
	}
	// The frames delivered before the stall are still reported: "cut after 2 frames"
	// is a different diagnosis from "cut before producing anything".
	if frames, ok := attrInt(stream, attrStreamFrames); !ok || frames == 0 {
		t.Errorf("%s = %d (set=%v), want the frames delivered before the stall",
			attrStreamFrames, frames, ok)
	}
}

// A stream that fails BEFORE the header produces a span with no first_frame.
//
// The negative case for TTFT: an event that is only ever present cannot
// distinguish "nothing was delivered" from "not instrumented", and this is the
// path where the client can still be sent a real error envelope.
func TestStreamFailingBeforeHeaderHasNoFirstFrame(t *testing.T) {
	sr := recordSpans(t)

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusBadRequest
	h := newHarness(t, realDefaults(), mcfg, nil)

	rec, _ := tracedRequest(t, h, sr, chatBody("gemini-2.5-flash", "fail early", true), nil)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400 (failed before any frame)\nbody: %s",
			rec.Code, rec.Body.String())
	}

	streams := spansNamed(sr, spanStream)
	if len(streams) != 1 {
		t.Fatalf("%q spans = %d, want 1 even when the stream never started",
			spanStream, len(streams))
	}
	stream := streams[0]

	if n := countEvent(stream, eventFirstFrame); n != 0 {
		t.Errorf("%q events = %d, want 0 — nothing reached the client", eventFirstFrame, n)
	}
	if frames, _ := attrInt(stream, attrStreamFrames); frames != 0 {
		t.Errorf("%s = %d, want 0", attrStreamFrames, frames)
	}
	// Not a deadline abort: the upstream refused outright, and labelling that as a
	// stream abort would inflate the metric that is supposed to mean "deadlines are
	// cutting streams".
	if reason, ok := attrString(stream, attrAbortReason); ok {
		t.Errorf("%s = %q for a pre-header upstream refusal; that is not a deadline "+
			"abort", attrAbortReason, reason)
	}
}
