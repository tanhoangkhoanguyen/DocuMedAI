package main

import (
	"context"
	"flag"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"

	"documedai/llmguard/internal/gateway"
	"documedai/llmguard/provider"
)

func main() {
	// `-healthcheck` mode: used by docker-compose's healthcheck. The distroless
	// runtime has no shell/curl, so the binary probes itself and exits 0/1.
	healthcheck := flag.Bool("healthcheck", false, "probe /healthz and exit")
	flag.Parse()
	if *healthcheck {
		runHealthcheck()
		return
	}

	// Structured JSON logging so logs are grep/Loki-friendly.
	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))

	cfg := gateway.LoadConfig()

	// The allowlist decides which models are callable, so it is required rather
	// than optional: without it every request would 400 while the health check
	// stayed green. Every validation problem is reported at once.
	mc, err := gateway.LoadModelConfig(cfg.ModelConfigPath)
	if err != nil {
		log.Error("model config", "path", cfg.ModelConfigPath, "err", err.Error())
		os.Exit(1)
	}

	// Resolve the providers up front: a process that cannot mint credentials
	// should fail at startup, not on the first request. For Vertex this reaches
	// out to Application Default Credentials.
	if err = gateway.SetupProviders(context.Background(), cfg, mc); err != nil {
		log.Error("provider setup failed", "err", err.Error())
		os.Exit(1)
	}
	log.Info("model allowlist loaded", "routes", provider.EnabledRoutes())

	// Redis backs the rate-limit token bucket (and the cross-replica dedup
	// extension point). Parse the URL form: redis://host:port/db.
	opt, err := redis.ParseURL(cfg.RedisURL)
	if err != nil {
		log.Error("invalid REDIS_URL", "err", err.Error())
		os.Exit(1)
	}
	rdb := redis.NewClient(opt)

	metrics := gateway.NewMetrics()
	limiter := gateway.NewRateLimiter(rdb, cfg.RateLimitRPM, cfg.RateLimitBurst)
	deduper := gateway.NewDeduper()
	// Shares the trip signal across replicas, so an upstream outage costs one
	// replica's worth of failed requests to detect rather than N. The flag's
	// lifetime is CircuitOpenFor — the same window the local breaker stays open —
	// so the two cannot disagree about how long "recently down" lasts.
	breakerSharer := gateway.NewBreakerSharer(rdb, cfg.CircuitOpenFor)
	proxy := gateway.NewProxy(cfg, limiter, deduper, breakerSharer, metrics, log)

	mux := http.NewServeMux()
	// All OpenAI-compatible traffic. Clients point their base_url at
	// http://la-llmguard:8081/v1, so requests arrive under /v1/*.
	mux.Handle("/v1/chat/completions", proxy)
	// Liveness for docker-compose healthcheck.
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	// Prometheus scrape endpoint.
	mux.Handle("/metrics", promhttp.Handler())

	srv := &http.Server{
		Addr:              ":" + cfg.Port,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,

		// Bounds an idle keep-alive connection. Without it a client that opens
		// connections and goes quiet holds one goroutine and one socket each for
		// as long as it likes. Admission control cannot see that: those requests
		// already completed and returned their slots, so the leak accumulates
		// entirely outside the in-flight ceiling.
		IdleTimeout: cfg.IdleTimeout,

		// WriteTimeout is deliberately LEFT UNSET, and that is a decision rather
		// than an omission.
		//
		// It is an absolute deadline measured from the start of the response, not
		// an inactivity timeout. A streaming completion legitimately writes for
		// minutes, so any value low enough to cut off a hung SSE reader would also
		// truncate healthy long streams — turning a rare leak into a routine
		// failure of the feature.
		//
		// The streaming path bounds itself instead, by inactivity rather than by
		// total duration (see internal/gateway/writedeadline.go and
		// idlewatchdog.go): a per-write deadline refreshed on each flushed frame
		// for a reader that stops reading, an inter-frame watchdog for an upstream
		// that goes quiet, and STREAM_ABSOLUTE_MAX as a backstop behind both.
		// Those measure the thing that actually matters — time since the last byte
		// moved — which a server-wide WriteTimeout cannot express.
	}

	// Graceful shutdown on SIGINT/SIGTERM so in-flight calls aren't cut off.
	go func() {
		log.Info("llmguard listening", "port", cfg.Port, "models", len(mc.ModelList))
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Error("server error", "err", err.Error())
			os.Exit(1)
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop

	log.Info("shutting down")
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	_ = srv.Shutdown(ctx)
	_ = rdb.Close()
}

// runHealthcheck performs a localhost GET /healthz and exits 0 on 200, 1 else.
// Invoked as `/llmguard -healthcheck` by the docker healthcheck.
func runHealthcheck() {
	port := gateway.Getenv("PROXY_PORT", "8081")
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://127.0.0.1:" + port + "/healthz")
	if err != nil || resp.StatusCode != http.StatusOK {
		os.Exit(1)
	}
	_ = resp.Body.Close()
	os.Exit(0)
}
