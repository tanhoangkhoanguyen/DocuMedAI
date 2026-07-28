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

	cfg := loadConfig()
	if cfg.OpenAIKey == "" {
		log.Error("upstream API key is required (set UPSTREAM_API_KEY, or OPENAI_API_KEY)")
		os.Exit(1)
	}

	// Redis backs the rate-limit token bucket (and the cross-replica dedup
	// extension point). Parse the URL form: redis://host:port/db.
	opt, err := redis.ParseURL(cfg.RedisURL)
	if err != nil {
		log.Error("invalid REDIS_URL", "err", err.Error())
		os.Exit(1)
	}
	rdb := redis.NewClient(opt)

	metrics := newMetrics()
	limiter := newRateLimiter(rdb, cfg.RateLimitRPM, cfg.RateLimitBurst)
	deduper := newDeduper()
	proxy := newProxy(cfg, limiter, deduper, metrics, log)

	mux := http.NewServeMux()
	// All OpenAI-compatible traffic. Clients point their base_url at
	// http://la-llmguard:8081/v1, so requests arrive under /v1/*.
	mux.Handle("/v1/", proxy)
	// Liveness for docker-compose healthcheck.
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	// Prometheus scrape endpoint.
	mux.Handle("/metrics", promhttp.Handler())

	srv := &http.Server{
		Addr:    ":" + cfg.Port,
		Handler: mux,
		// No global write timeout: LLM calls are long. Per-attempt timeout is
		// enforced by the upstream http.Client instead.
		ReadHeaderTimeout: 10 * time.Second,
	}

	// Graceful shutdown on SIGINT/SIGTERM so in-flight calls aren't cut off.
	go func() {
		log.Info("llmguard listening", "port", cfg.Port, "upstream", cfg.UpstreamBase)
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
	port := getenv("PROXY_PORT", "8081")
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://127.0.0.1:" + port + "/healthz")
	if err != nil || resp.StatusCode != http.StatusOK {
		os.Exit(1)
	}
	_ = resp.Body.Close()
	os.Exit(0)
}
