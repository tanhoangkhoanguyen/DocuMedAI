package gateway

// Guards the harness/production config invariant.
//
// harness_test.go's realDefaults() exists so the characterization suite asserts
// against the SAME thresholds production runs with — RetryMax=4, CircuitMinReqs=10,
// CircuitFailRatio=0.6, RateLimitBurst=60. Its doc comment states that a threshold
// change in config.go should surface as a test failure rather than passing against
// a stale copy, but nothing enforced that: realDefaults() is a hand-written literal,
// so the two could drift apart silently and every threshold assertion in the suite
// would keep passing against numbers production no longer uses.
//
// This test makes the invariant load-bearing instead of aspirational.

import (
	"reflect"
	"testing"
)

// resilienceFields are the Config fields realDefaults() deliberately mirrors:
// the retry / breaker / rate-limit / upstream-client / streaming-deadline knobs
// the characterization tests assert thresholds against.
//
// The fields realDefaults() omits are excluded on purpose, because the
// harness has no server and no Redis to configure:
//
//	VertexProject  - no credentials; the mock provider builds its own URL
//	VertexLocation - same
//	ModelConfigPath - the harness registers its adapter and routes directly,
//	                  so there is no YAML file to point at
//	Port           - the harness drives ServeHTTP directly, it never listens
//	RedisURL       - offlineLimiter() points at a dead address on purpose
//	IdleTimeout    - a server knob; the harness has no http.Server, same as Port
//	MaxInFlight    - mirroring it would apply a concurrency ceiling to every test
//	                 in the suite, none of which is about concurrency; the
//	                 admission tests set it explicitly instead. See the skip list.
var resilienceFields = []string{
	"RateLimitRPM",
	"RateLimitBurst",
	"RateWaitMax",
	"RetryMax",
	"RetryBaseDly",
	"RetryMaxDly",
	"CircuitMinReqs",
	"CircuitFailRatio",
	"CircuitOpenFor",
	"UpstreamTimeout",
	"MaxIdleConns",
	"StreamWriteIdle",
	"StreamIdleTimeout",
	"StreamAbsoluteMax",
}

// TestRealDefaultsMatchLoadConfig fails when a production default changes without
// realDefaults() following it.
//
// loadConfig() reads the environment, so every var it consults is cleared first —
// otherwise a developer's shell (or CI's throwaway .env) would be compared against
// the harness instead of the compiled-in defaults. t.Setenv restores each one at
// test end, and forbids t.Parallel, which is why this test does not use it.
func TestRealDefaultsMatchLoadConfig(t *testing.T) {
	for _, key := range []string{
		"LLMGUARD_PROVIDER", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION",
		"PROXY_PORT", "REDIS_URL",
		"RATE_LIMIT_RPM", "RATE_LIMIT_BURST", "RATE_WAIT_MAX",
		"RETRY_MAX", "RETRY_BASE_DELAY", "RETRY_MAX_DELAY",
		"CIRCUIT_MIN_REQUESTS", "CIRCUIT_FAIL_RATIO", "CIRCUIT_OPEN_FOR",
		"UPSTREAM_TIMEOUT", "MAX_IDLE_CONNS",
		"MAX_IN_FLIGHT", "SERVER_IDLE_TIMEOUT",
		"STREAM_WRITE_IDLE", "STREAM_IDLE_TIMEOUT", "STREAM_ABSOLUTE_MAX",
		"OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_TRACES_SAMPLER_ARG",
		"OTEL_SERVICE_NAME", "OTEL_SHUTDOWN_GRACE",
	} {
		t.Setenv(key, "")
	}

	prod := reflect.ValueOf(loadConfig())
	harness := reflect.ValueOf(realDefaults())

	for _, name := range resilienceFields {
		want := prod.FieldByName(name)
		if !want.IsValid() {
			t.Fatalf("Config has no field %q — update resilienceFields", name)
		}
		if got := harness.FieldByName(name); !reflect.DeepEqual(got.Interface(), want.Interface()) {
			t.Errorf("realDefaults().%s = %v, loadConfig().%s = %v\n"+
				"harness_test.go's realDefaults() must mirror config.go's production default",
				name, got.Interface(), name, want.Interface())
		}
	}
}

// TestResilienceFieldsCoversConfig fails when a knob is added to Config without a
// decision about whether the harness should mirror it.
//
// Without this, a new field defaults to the zero value in realDefaults() and every
// test silently runs against 0 — a new timeout knob would read as "no timeout"
// rather than as a missing default.
func TestResilienceFieldsCoversConfig(t *testing.T) {
	// Fields the harness intentionally does not mirror; see resilienceFields.
	//
	// MaxInFlight is skipped rather than mirrored for a reason worth stating: it is
	// a real production default (256), not an unconfigured knob. Mirroring it would
	// impose a concurrency ceiling on the whole characterization suite, where the
	// breaker tests deliberately run many requests at once to observe
	// tripping. A shed request there would look like a retry that
	// never happened. Leaving it 0 keeps those tests measuring what they were
	// written to measure; admission_test.go sets the ceiling per test.
	//
	// The four Trace* knobs are skipped for a different reason than the rest: they
	// are not resilience thresholds at all. They change what LLMGuard REPORTS about
	// a request, never how it retries, sheds or trips, so no characterization test
	// can be affected by their value. Mirroring them would also configure an
	// exporter no test reads — the harness drives ServeHTTP under the global no-op
	// tracer, and tracing_test.go installs its own recorder when it wants spans.
	skipped := map[string]bool{
		"VertexProject": true, "VertexLocation": true, "ModelConfigPath": true,
		"Port": true, "RedisURL": true,
		"IdleTimeout": true, "MaxInFlight": true,
		"TraceEndpoint": true, "TraceSampleRatio": true,
		"TraceServiceName": true, "TraceShutdownGrace": true,
	}

	covered := make(map[string]bool, len(resilienceFields))
	for _, name := range resilienceFields {
		covered[name] = true
	}

	typ := reflect.TypeOf(Config{})
	for i := range typ.NumField() {
		name := typ.Field(i).Name
		if !covered[name] && !skipped[name] {
			t.Errorf("Config.%s is neither in resilienceFields nor explicitly skipped.\n"+
				"Add it to realDefaults() + resilienceFields, or to the skip list with a reason.",
				name)
		}
	}
}
