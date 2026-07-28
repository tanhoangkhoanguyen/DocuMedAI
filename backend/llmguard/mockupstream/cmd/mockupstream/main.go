// Command mockupstream runs the deterministic mock LLM provider as a standalone
// process, for resilience tests and benchmarks that need a real socket.
//
// The binary is a thin shell: every behavior lives in the mockupstream package
// so an in-process test fake and this process serve byte-identical responses.
package main

import (
	"context"
	"errors"
	"flag"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"documedai/llm-proxy/mockupstream"
)

func main() {
	def := mockupstream.FromEnv(mockupstream.DefaultConfig())

	// Flags override the environment, which overrides the built-in defaults.
	addr := flag.String("addr", def.Addr, "listen address")
	// Mirrors the proxy's own convention: the distroless runtime has no shell or
	// curl, so the binary probes itself for the compose healthcheck.
	healthcheck := flag.Bool("healthcheck", false, "probe /healthz and exit 0/1")
	latency := flag.Duration("latency", def.Latency, "fixed latency added to every response")
	jitter := flag.Duration("jitter", def.Jitter, "width of additional random latency [0,jitter]")
	chunkDelay := flag.Duration("chunk-delay", def.ChunkDelay, "pause between streamed chunks")
	errorRate := flag.Float64("error-rate", def.ErrorRate, "fraction of requests [0,1] that fail")
	errorStatus := flag.Int("error-status", def.ErrorStatus, "status code for injected failures")
	retryAfter := flag.Int("retry-after", def.RetryAfter, "Retry-After seconds on failures (0 = omit)")
	seed := flag.Uint64("seed", def.Seed, "base RNG seed; changes which requests fail, not how many")
	model := flag.String("model", def.Model, "model name echoed when the request names none")
	completionTokens := flag.Int("completion-tokens", def.CompletionTokens, "words in the canned reply")
	content := flag.String("content", def.Content, "fixed reply text (overrides generation)")
	outage := flag.Duration("outage", 0, "open a total-outage window at startup")
	flag.Parse()

	if *healthcheck {
		runHealthcheck(*addr)
		return
	}

	cfg := mockupstream.Config{
		Addr:             *addr,
		Latency:          *latency,
		Jitter:           *jitter,
		ChunkDelay:       *chunkDelay,
		ErrorRate:        *errorRate,
		ErrorStatus:      *errorStatus,
		RetryAfter:       *retryAfter,
		Seed:             *seed,
		Model:            *model,
		Created:          def.Created,
		CompletionTokens: *completionTokens,
		Content:          *content,
	}

	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	srv := mockupstream.New(cfg)
	if *outage > 0 {
		srv.StartOutage(*outage)
	}

	httpSrv := &http.Server{
		Addr:    cfg.Addr,
		Handler: srv,
		// Injected latency can legitimately exceed any write deadline, so only
		// the header read is bounded — the same reasoning as the proxy itself.
		ReadHeaderTimeout: 10 * time.Second,
	}

	go func() {
		log.Info("mockupstream listening",
			"addr", cfg.Addr,
			"latency", cfg.Latency.String(),
			"jitter", cfg.Jitter.String(),
			"error_rate", cfg.ErrorRate,
			"seed", cfg.Seed,
		)
		if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("server error", "err", err.Error())
			os.Exit(1)
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop

	log.Info("shutting down")
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = httpSrv.Shutdown(ctx)
}

// runHealthcheck performs a loopback GET /healthz and exits 0 on 200, 1 else.
// Invoked as `/mockupstream -healthcheck` by the container healthcheck.
//
// It probes /healthz specifically because that endpoint reports the mock's own
// liveness and stays 200 during a simulated outage — a healthcheck that failed
// whenever a test injected an outage would restart the container mid-test.
func runHealthcheck(addr string) {
	port := strings.TrimPrefix(addr, ":")
	if idx := strings.LastIndex(addr, ":"); idx >= 0 {
		port = addr[idx+1:]
	}
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://127.0.0.1:" + port + "/healthz")
	if err != nil {
		os.Exit(1)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		os.Exit(1)
	}
	os.Exit(0)
}
