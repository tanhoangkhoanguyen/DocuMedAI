package gateway

import (
	"os"
	"strconv"
	"time"
)

// Config holds every tunable knob LLMGuard reads from the environment.
//
// In docker-compose these come from `.env` + explicit `environment:` overrides
// on the `la-llmguard` service. Defaults are chosen so it boots with
// nothing but OPENAI_API_KEY set.
type Config struct {
	// VertexProject / VertexLocation configure the Vertex AI adapter. These are
	// the SAME env vars the Python backend reads (backend/utils/llm_config.py),
	// so one .env configures both. Auth is Application Default Credentials —
	// there is no API key: Vertex will not accept one.
	VertexProject  string
	VertexLocation string

	// ModelConfigPath points at the YAML model allowlist (see modelconfig.go).
	// Read from LLMGUARD_CONFIG.
	//
	// Required: the allowlist is the only authority over which models are
	// callable, so main exits when it is missing or invalid.
	ModelConfigPath string

	// Port the proxy listens on (matches docker-compose + the client base_url).
	Port string

	// RedisURL backs the distributed token bucket and the cross-process dedup
	// marker. We use DB 1 so we never collide with the app's chat cache on DB 0.
	RedisURL string

	// --- Rate limiting (token bucket, per API key + model) ---
	RateLimitRPM   int           // sustained requests/min that refill the bucket
	RateLimitBurst int           // max tokens the bucket can hold (allows short bursts)
	RateWaitMax    time.Duration // how long a request blocks for a token before we 429

	// --- Retry / backoff ---
	RetryMax     int           // max attempts (1 = no retry)
	RetryBaseDly time.Duration // base delay for exponential backoff
	RetryMaxDly  time.Duration // cap on a single backoff sleep

	// --- Circuit breaker ---
	CircuitMinReqs   uint32        // min requests in a window before the breaker may trip
	CircuitFailRatio float64       // fraction of failures that trips the breaker
	CircuitOpenFor   time.Duration // how long the breaker stays open before half-open probe

	// --- Admission control ---
	//
	// MaxInFlight caps CONCURRENT requests, which is a different quantity from the
	// rate limit's requests-per-minute and is the one that maps to memory: each
	// in-flight request holds a goroutine, a response buffer up to
	// maxUpstreamBody, and an upstream connection. Arrival rate says nothing about
	// how many are running when upstream slows down.
	//
	// Tune it as: (RateLimitRPM / 60) × p95_upstream_seconds × 1.5. The default
	// 256 is that formula at 480 RPM and a 20s p95, so a bucket-legal burst is
	// never shed — it only engages when requests pile up faster than they drain,
	// or when Redis is down and the limiter is failing open.
	//
	// 0 or less disables admission control, restoring the unbounded behavior for
	// an operator who wants it.
	MaxInFlight int

	// --- Upstream HTTP client ---
	UpstreamTimeout time.Duration // per-attempt timeout to upstream
	MaxIdleConns    int           // connection-pool size for keep-alive reuse

	// --- Streaming deadlines ---
	//
	// A streaming request holds its admission slot for the whole life of the
	// stream, so whatever bounds that lifetime is what bounds the ceiling. The
	// buffered path's UpstreamTimeout cannot do it: http.Client.Timeout is an
	// ABSOLUTE deadline covering the body read, so a value low enough to cut off a
	// stalled stream also truncates a healthy long one. The right quantity for a
	// stream is INACTIVITY — time since the last byte moved — which is what the
	// first two knobs below measure.
	//
	// Each accepts 0 to disable it, matching MaxInFlight's convention that an
	// operator can explicitly ask for the older unbounded behavior.

	// StreamWriteIdle bounds how long a write to the CLIENT may stall.
	//
	// The failure it closes: a client opens a stream and stops reading. The TCP
	// send buffer fills, flusher.Flush() blocks, and r.Context() never fires
	// because the client never closed the socket — it is silent, not gone. The
	// slot is held until upstream finishes on its own.
	//
	// Applied as a per-write deadline REFRESHED after every flushed frame
	// (http.ResponseController), so a stream that keeps moving never trips it no
	// matter how long it runs. 30s is far above any real client's scheduling
	// hiccup and far below the cost of pinning a slot.
	StreamWriteIdle time.Duration

	// StreamIdleTimeout bounds the gap between two frames FROM UPSTREAM.
	//
	// The failure it closes: a slow-loris upstream that emits a byte every few
	// minutes. Nothing else measures inter-frame time, so the slot is held at the
	// upstream's pace rather than at any limit of ours.
	//
	// 60s, i.e. double StreamWriteIdle: a provider's time-to-first-token under
	// load is legitimately tens of seconds, and cutting a real generation is worse
	// than holding a slot a little longer.
	StreamIdleTimeout time.Duration

	// StreamAbsoluteMax is a backstop on total stream duration, applied as the
	// streaming client's Timeout.
	//
	// The two inactivity bounds above are the real protection and this should
	// never fire. It exists because both can fail together — SetWriteDeadline
	// degrades to a no-op behind a ResponseWriter that does not implement it, and
	// an upstream that dribbles one frame per second is "active" by the
	// inter-frame measure while still holding a slot indefinitely. Without a
	// backstop that combination pins a slot for the process's lifetime and only a
	// restart returns it; with one it self-heals.
	//
	// 30m is chosen to be unreachable by real traffic — orders of magnitude above
	// the longest plausible completion — so it only ever truncates a stream that
	// was already pathological.
	StreamAbsoluteMax time.Duration

	// --- HTTP server ---
	//
	// IdleTimeout bounds how long an idle keep-alive connection is held. Without
	// it a client that opens connections and goes quiet pins one goroutine and one
	// socket each, indefinitely — a slow resource leak that admission control
	// cannot see, because those requests already finished.
	//
	// There is deliberately no WriteTimeout: see main.go.
	IdleTimeout time.Duration

	// --- Tracing (OpenTelemetry) ---
	//
	// TraceEndpoint is the OTLP/HTTP collector base URL, e.g. http://la-jaeger:4318.
	//
	// EMPTY DISABLES TRACING ENTIRELY, and that is the whole switch: no SDK provider
	// is installed and the global tracer stays OpenTelemetry's no-op. Read with a
	// bare os.Getenv rather than getenv(key, def) because "" is the meaningful value
	// here, not a missing one.
	//
	// The SDK reads this same variable itself and appends /v1/traces (the sibling
	// OTEL_EXPORTER_OTLP_TRACES_ENDPOINT is used verbatim instead), so the exporter
	// is built with no endpoint option — see tracing.go.
	TraceEndpoint string

	// TraceSampleRatio is the head-sampling probability in [0,1] for traces this
	// process STARTS. An inbound sampled decision is inherited, so lowering this
	// can never truncate a trace that arrived already sampled.
	//
	// Defaults to 1.0 (trace everything), which suits a low-QPS internal gateway
	// where the interesting request is rare and losing it to a coin flip defeats the
	// purpose. Note 0 is NOT the cheap way to switch tracing off: the SDK still
	// builds a span before the sampler drops it, measured at ~17x the no-op path.
	// Leave TraceEndpoint empty instead.
	TraceSampleRatio float64

	// TraceServiceName is the service.name resource attribute — the name this
	// process appears under in a trace UI's service list.
	TraceServiceName string

	// TraceShutdownGrace bounds the final flush of buffered spans at shutdown.
	//
	// Spans leave in batches, so without a flush the last few seconds of traces are
	// lost — exactly the window that matters when a process is going down. Kept well
	// inside main.go's 15s shutdown budget so a collector that has itself gone away
	// can never be the reason a drain times out.
	TraceShutdownGrace time.Duration
}

// loadConfig reads the environment and applies sensible production defaults.
func loadConfig() Config {
	return Config{
		VertexProject:   os.Getenv("GOOGLE_CLOUD_PROJECT"),
		VertexLocation:  getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
		ModelConfigPath: getenv("LLMGUARD_CONFIG", "config.yaml"),
		Port:            getenv("PROXY_PORT", "8081"),
		RedisURL:        getenv("REDIS_URL", "redis://la-redis:6379/1"),

		RateLimitRPM:   getenvInt("RATE_LIMIT_RPM", 480), // 8 req/s sustained
		RateLimitBurst: getenvInt("RATE_LIMIT_BURST", 60),
		RateWaitMax:    getenvDur("RATE_WAIT_MAX", 5*time.Second),

		RetryMax:     getenvInt("RETRY_MAX", 4),
		RetryBaseDly: getenvDur("RETRY_BASE_DELAY", 300*time.Millisecond),
		RetryMaxDly:  getenvDur("RETRY_MAX_DELAY", 8*time.Second),

		CircuitMinReqs:   uint32(getenvInt("CIRCUIT_MIN_REQUESTS", 10)),
		CircuitFailRatio: getenvFloat("CIRCUIT_FAIL_RATIO", 0.6),
		CircuitOpenFor:   getenvDur("CIRCUIT_OPEN_FOR", 20*time.Second),

		MaxInFlight: getenvInt("MAX_IN_FLIGHT", 256),

		UpstreamTimeout: getenvDur("UPSTREAM_TIMEOUT", 120*time.Second), // LLM calls can be slow
		MaxIdleConns:    getenvInt("MAX_IDLE_CONNS", 100),

		StreamWriteIdle:   getenvDur("STREAM_WRITE_IDLE", 30*time.Second),
		StreamIdleTimeout: getenvDur("STREAM_IDLE_TIMEOUT", 60*time.Second),
		StreamAbsoluteMax: getenvDur("STREAM_ABSOLUTE_MAX", 30*time.Minute),

		IdleTimeout: getenvDur("SERVER_IDLE_TIMEOUT", 120*time.Second),

		// Bare os.Getenv: empty means "tracing off", so there is no default to
		// fall back to. The other three only matter once this is set.
		TraceEndpoint:      os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
		TraceSampleRatio:   getenvFloat("OTEL_TRACES_SAMPLER_ARG", 1.0),
		TraceServiceName:   getenv("OTEL_SERVICE_NAME", "llmguard"),
		TraceShutdownGrace: getenvDur("OTEL_SHUTDOWN_GRACE", 5*time.Second),
	}
}

// --- tiny env helpers (kept here so the rest of the code stays clean) ---
func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func getenvInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func getenvFloat(key string, def float64) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}

func getenvDur(key string, def time.Duration) time.Duration {
	if v := os.Getenv(key); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
	}
	return def
}
