package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/sony/gobreaker"

	"documedai/llmguard/provider"
)

// Proxy is the HTTP handler for /v1/chat/completions. It owns the
// provider-agnostic concerns — rate limit → dedup → circuit breaker → retry —
// and delegates every vendor-specific detail (URL, auth, wire format) to a
// provider.Provider.
type Proxy struct {
	cfg     Config
	client  *http.Client // shared, keep-alive pooled
	limiter *RateLimiter
	deduper *Deduper
	breaker *gobreaker.CircuitBreaker
	metrics *Metrics
	log     *slog.Logger
}

func newProxy(cfg Config, limiter *RateLimiter, deduper *Deduper, m *Metrics, log *slog.Logger) *Proxy {
	// One shared client with a tuned transport so TCP/TLS connections upstream
	// are reused across requests instead of re-handshaking every call.
	transport := &http.Transport{
		MaxIdleConns:        cfg.MaxIdleConns,
		MaxIdleConnsPerHost: cfg.MaxIdleConns,
		IdleConnTimeout:     90 * time.Second,
		ForceAttemptHTTP2:   true,
	}
	return &Proxy{
		cfg:     cfg,
		client:  &http.Client{Transport: transport, Timeout: cfg.UpstreamTimeout},
		limiter: limiter,
		deduper: deduper,
		breaker: newBreaker(cfg, m),
		metrics: m,
		log:     log,
	}
}

// ServeHTTP decodes the OpenAI-shaped request, resolves its provider, and
// dispatches to the streaming or buffered path.
func (p *Proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	start := time.Now()

	if r.Method != http.MethodPost {
		p.writeError(w, "unknown", start, http.StatusMethodNotAllowed,
			"method not allowed", "invalid_request_error")
		return
	}

	body, err := io.ReadAll(r.Body)
	if err != nil {
		p.writeError(w, "unknown", start, http.StatusBadRequest,
			"failed to read request body", "invalid_request_error")
		return
	}
	_ = r.Body.Close()

	var req provider.ChatRequest
	if decErr := json.Unmarshal(body, &req); decErr != nil {
		p.writeError(w, "unknown", start, http.StatusBadRequest,
			"invalid JSON body", "invalid_request_error")
		return
	}
	model := req.Model
	if model == "" {
		p.writeError(w, "unknown", start, http.StatusBadRequest,
			"field 'model' is required", "invalid_request_error")
		return
	}
	if len(req.Messages) == 0 {
		p.writeError(w, model, start, http.StatusBadRequest,
			"field 'messages' must not be empty", "invalid_request_error")
		return
	}

	prov, err := provider.For(model, p.cfg.Provider)
	if err != nil {
		p.writeError(w, model, start, http.StatusBadRequest, err.Error(), "invalid_request_error")
		return
	}

	// --- Rate limit (token bucket, per key+model) ---
	rlKey := apiKeyHint(r) + ":" + model
	if !p.limiter.Acquire(r.Context(), rlKey, p.cfg.RateWaitMax) {
		p.metrics.rateLimited.WithLabelValues(model).Inc()
		p.writeError(w, model, start, http.StatusTooManyRequests,
			"proxy rate limit exceeded", "rate_limit")
		return
	}

	// Streaming requests cannot be buffered/deduped/replayed as a unit — they
	// get breaker protection but no retry/dedup.
	if req.Stream {
		p.serveStreaming(w, r, prov, &req, start)
		return
	}
	p.serveBuffered(w, r, prov, &req, body, start)
}

// serveBuffered handles the normal (non-streaming) path: dedup → breaker → retry.
func (p *Proxy) serveBuffered(
	w http.ResponseWriter, r *http.Request,
	prov provider.Provider, req *provider.ChatRequest, rawBody []byte, start time.Time,
) {
	model := req.Model
	key := dedupKey(rawBody)

	res, shared, err := p.deduper.Do(key, func() (*upstreamResult, error) {
		// The breaker wraps the WHOLE retry loop: a tripped breaker should stop
		// us before we even start retrying.
		v, berr := p.breaker.Execute(func() (interface{}, error) {
			return doWithRetry(r.Context(), p.cfg, key,
				func() { p.metrics.retries.WithLabelValues(model).Inc() },
				func(ctx context.Context) (*upstreamResult, error) {
					return p.forwardBuffered(ctx, prov, req)
				},
			)
		})
		if berr != nil {
			return nil, berr
		}
		return v.(*upstreamResult), nil
	})

	if shared {
		p.metrics.dedupHits.Inc()
	}

	if err != nil {
		// A translated upstream error carries the vendor's own message and
		// status; anything else (breaker open, transport failure) is a 503.
		var ue *provider.UpstreamError
		if errors.As(err, &ue) {
			p.writeError(w, model, start, ue.Status, ue.Body.Error.Message, ue.Body.Error.Type)
			return
		}
		p.log.Warn("upstream failed", "model", model, "err", err.Error())
		p.writeError(w, model, start, http.StatusServiceUnavailable,
			"upstream unavailable", "upstream_error")
		return
	}

	p.recordUsage(model, res.usage)
	p.writeJSON(w, model, start, res.status, res.body, "buffered")
}

// forwardBuffered performs ONE upstream attempt: build → send → translate. The
// translated response is buffered so it can be retried and deduped.
func (p *Proxy) forwardBuffered(
	ctx context.Context, prov provider.Provider, req *provider.ChatRequest,
) (*upstreamResult, error) {
	httpReq, err := prov.BuildRequest(ctx, req)
	if err != nil {
		return nil, err
	}
	resp, err := p.client.Do(httpReq)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	nativeBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	translated, err := prov.TranslateResponse(resp.StatusCode, nativeBody)
	if err != nil {
		// Surface the status so the retry loop can decide (429/5xx retryable).
		var ue *provider.UpstreamError
		if errors.As(err, &ue) {
			return &upstreamResult{status: ue.Status, header: resp.Header.Clone()}, err
		}
		return nil, err
	}

	out, err := json.Marshal(translated)
	if err != nil {
		return nil, err
	}
	return &upstreamResult{
		status: http.StatusOK,
		header: resp.Header.Clone(),
		body:   out,
		usage:  translated.Usage,
	}, nil
}

// serveStreaming translates the provider's SSE stream into OpenAI chunks,
// flushing each as it arrives and terminating with `data: [DONE]`.
func (p *Proxy) serveStreaming(
	w http.ResponseWriter, r *http.Request,
	prov provider.Provider, req *provider.ChatRequest, start time.Time,
) {
	model := req.Model
	flusher, ok := w.(http.Flusher)
	if !ok {
		p.writeError(w, model, start, http.StatusInternalServerError,
			"streaming unsupported", "upstream_error")
		return
	}

	var usage provider.Usage
	_, err := p.breaker.Execute(func() (interface{}, error) {
		httpReq, berr := prov.BuildRequest(r.Context(), req)
		if berr != nil {
			return nil, berr
		}
		resp, berr := p.client.Do(httpReq)
		if berr != nil {
			return nil, berr
		}
		defer resp.Body.Close()

		if resp.StatusCode < 200 || resp.StatusCode > 299 {
			nativeBody, _ := io.ReadAll(resp.Body)
			// Reuse the adapter's error translation: a non-2xx status makes it
			// return an *UpstreamError carrying the vendor's message.
			_, terr := prov.TranslateResponse(resp.StatusCode, nativeBody)
			if terr != nil {
				return nil, terr
			}
			return nil, errors.New("upstream error")
		}

		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		w.WriteHeader(http.StatusOK)
		p.metrics.requests.WithLabelValues(model, statusLabel(http.StatusOK)).Inc()

		// Scan the provider's SSE frames line by line. Vertex sends
		// `data: {...}` per frame; blank lines separate events.
		scanner := bufio.NewScanner(resp.Body)
		scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024) // a frame can be large
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line == "" || !strings.HasPrefix(line, "data:") {
				continue
			}
			payload := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
			if payload == "[DONE]" {
				continue // we emit our own terminator
			}

			chunks, cerr := prov.TranslateStreamChunk(req, []byte(payload))
			if cerr != nil {
				p.log.Warn("stream chunk translate failed", "model", model, "err", cerr.Error())
				continue // a malformed frame shouldn't kill the whole stream
			}
			for _, ch := range chunks {
				if ch.Usage != nil {
					usage = *ch.Usage
				}
				enc, merr := json.Marshal(ch)
				if merr != nil {
					continue
				}
				_, _ = w.Write([]byte("data: "))
				_, _ = w.Write(enc)
				_, _ = w.Write([]byte("\n\n"))
				flusher.Flush()
			}
		}
		if serr := scanner.Err(); serr != nil {
			return nil, serr
		}

		_, _ = w.Write([]byte("data: [DONE]\n\n"))
		flusher.Flush()
		return nil, nil
	})
	if err != nil {
		p.log.Warn("streaming upstream failed", "model", model, "err", err.Error())
	}

	// The streaming path now accounts tokens too — the old byte-pipe could not.
	p.recordUsage(model, usage)
	p.metrics.latency.WithLabelValues(model).Observe(time.Since(start).Seconds())
}

// recordUsage adds normalized token counts to metrics.
func (p *Proxy) recordUsage(model string, u provider.Usage) {
	if u.PromptTokens > 0 {
		p.metrics.tokensUsed.WithLabelValues(model, "prompt").Add(float64(u.PromptTokens))
	}
	if u.CompletionTokens > 0 {
		p.metrics.tokensUsed.WithLabelValues(model, "completion").Add(float64(u.CompletionTokens))
	}
}

// writeJSON writes a JSON body and records metrics/log.
func (p *Proxy) writeJSON(
	w http.ResponseWriter, model string, start time.Time, status int, body []byte, kind string,
) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)

	p.metrics.requests.WithLabelValues(model, statusLabel(status)).Inc()
	p.metrics.latency.WithLabelValues(model).Observe(time.Since(start).Seconds())
	p.log.Info("request",
		"model", model,
		"status", status,
		"latency_ms", time.Since(start).Milliseconds(),
		"kind", kind,
	)
}

// writeError emits the OpenAI-shaped error envelope so clients see one error
// format regardless of which provider (or LLMGuard itself) produced it.
func (p *Proxy) writeError(
	w http.ResponseWriter, model string, start time.Time, status int, msg, typ string,
) {
	body, err := json.Marshal(provider.NewErrorEnvelope(msg, typ))
	if err != nil {
		body = []byte(`{"error":{"message":"internal error","type":"upstream_error"}}`)
	}
	p.writeJSON(w, model, start, status, body, "error")
}

// --- small helpers ---

// apiKeyHint derives a short, non-secret bucket label from the caller's key so
// rate-limit buckets are per-key without logging the key itself.
func apiKeyHint(r *http.Request) string {
	auth := r.Header.Get("Authorization")
	auth = strings.TrimPrefix(auth, "Bearer ")
	if len(auth) <= 8 {
		return "anon"
	}
	return auth[len(auth)-6:] // last 6 chars — stable, low-collision, not the secret
}

func statusLabel(status int) string {
	switch {
	case status >= 500:
		return "5xx"
	case status == 429:
		return "429"
	case status >= 400:
		return "4xx"
	default:
		return "2xx"
	}
}
