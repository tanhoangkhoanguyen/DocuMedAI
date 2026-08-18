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
	"testing"
	"time"

	"go.opentelemetry.io/otel"

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
