package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/sony/gobreaker"
)

// Proxy is the HTTP handler that forwards OpenAI-compatible requests upstream,
// applying (in order): rate limit → dedup → circuit breaker → retry/backoff.
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
	// One shared client with a tuned transport so TCP/TLS connections to OpenAI
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

// requestMeta is the slice of the request body we care about for routing
// decisions: which model, and whether the caller asked for a stream.
type requestMeta struct {
	Model  string `json:"model"`
	Stream bool   `json:"stream"`
}

// ServeHTTP handles every /v1/* path. It buffers the request body once (so it
// can be replayed on retry and hashed for dedup), then dispatches to the
// streaming or buffered path.
func (p *Proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	start := time.Now()

	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "failed to read request body", http.StatusBadRequest)
		return
	}
	_ = r.Body.Close()

	// Peek at model + stream flag. Non-JSON bodies (rare) default to empty meta.
	var meta requestMeta
	_ = json.Unmarshal(body, &meta)
	model := meta.Model
	if model == "" {
		model = "unknown"
	}

	// --- Rate limit (token bucket, per key+model) ---
	rlKey := apiKeyHint(r) + ":" + model
	if !p.limiter.Acquire(r.Context(), rlKey, p.cfg.RateWaitMax) {
		p.metrics.rateLimited.WithLabelValues(model).Inc()
		p.finish(w, model, start, http.StatusTooManyRequests, nil, []byte(`{"error":{"message":"proxy rate limit exceeded","type":"rate_limit"}}`), "json")
		return
	}

	// Streaming requests cannot be buffered/deduped/replayed as a unit — we pass
	// them through with breaker protection but no retry/dedup. (Forward-compat:
	// the current backend never sets stream=true, see plan.)
	if meta.Stream {
		p.serveStreaming(w, r, body, model, start)
		return
	}

	p.serveBuffered(w, r, body, model, start)
}

// serveBuffered handles the normal (non-streaming) path: dedup → breaker → retry.
func (p *Proxy) serveBuffered(w http.ResponseWriter, r *http.Request, body []byte, model string, start time.Time) {
	key := dedupKey(body)

	res, shared, err := p.deduper.Do(key, func() (*upstreamResult, error) {
		// The breaker wraps the WHOLE retry loop: a tripped breaker should stop
		// us before we even start retrying.
		v, berr := p.breaker.Execute(func() (interface{}, error) {
			return doWithRetry(r.Context(), p.cfg, key,
				func() { p.metrics.retries.WithLabelValues(model).Inc() },
				func(ctx context.Context) (*upstreamResult, error) {
					return p.forwardBuffered(ctx, r, body)
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
		// Breaker open or total failure → fail fast with 503.
		status := http.StatusServiceUnavailable
		p.log.Warn("upstream failed", "model", model, "err", err.Error())
		p.finish(w, model, start, status, nil, []byte(`{"error":{"message":"upstream unavailable","type":"upstream_error"}}`), "json")
		return
	}

	p.recordUsage(model, res.body)
	p.finish(w, model, start, res.status, res.header, res.body, "passthrough")
}

// forwardBuffered performs ONE upstream attempt and reads the full response into
// memory so it can be retried/deduped. Returns a non-nil result even for
// retryable statuses so the retry loop can inspect Retry-After.
func (p *Proxy) forwardBuffered(ctx context.Context, r *http.Request, body []byte) (*upstreamResult, error) {
	req, err := p.buildUpstreamRequest(ctx, r, body)
	if err != nil {
		return nil, err
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	return &upstreamResult{status: resp.StatusCode, header: resp.Header.Clone(), body: respBody}, nil
}

// serveStreaming pipes an SSE response straight through, flushing each chunk as
// it arrives (diagram box 6). No retry/dedup — see ServeHTTP note.
func (p *Proxy) serveStreaming(w http.ResponseWriter, r *http.Request, body []byte, model string, start time.Time) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}

	_, err := p.breaker.Execute(func() (interface{}, error) {
		req, berr := p.buildUpstreamRequest(r.Context(), r, body)
		if berr != nil {
			return nil, berr
		}
		resp, berr := p.client.Do(req)
		if berr != nil {
			return nil, berr
		}
		defer resp.Body.Close()

		// Copy status + headers, then stream the body with per-chunk flushes.
		copyHeaders(w.Header(), resp.Header)
		w.WriteHeader(resp.StatusCode)
		p.metrics.requests.WithLabelValues(model, statusLabel(resp.StatusCode)).Inc()

		buf := make([]byte, 4096)
		for {
			n, rerr := resp.Body.Read(buf)
			if n > 0 {
				_, _ = w.Write(buf[:n])
				flusher.Flush()
			}
			if rerr != nil {
				break // io.EOF on clean end
			}
		}
		return nil, nil
	})
	if err != nil {
		p.log.Warn("streaming upstream failed", "model", model, "err", err.Error())
	}
	p.metrics.latency.WithLabelValues(model).Observe(time.Since(start).Seconds())
}

// buildUpstreamRequest clones the inbound request toward upstream, swapping the
// path under UpstreamBase and injecting the REAL OpenAI key. The backend's own
// (possibly dummy) Authorization header is discarded.
func (p *Proxy) buildUpstreamRequest(ctx context.Context, r *http.Request, body []byte) (*http.Request, error) {
	// r.URL.Path is like "/v1/chat/completions"; UpstreamBase already ends in
	// "/v1", so strip a leading "/v1" to avoid doubling it.
	path := strings.TrimPrefix(r.URL.Path, "/v1")
	url := p.cfg.UpstreamBase + path
	if r.URL.RawQuery != "" {
		url += "?" + r.URL.RawQuery
	}

	req, err := http.NewRequestWithContext(ctx, r.Method, url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	// Forward content headers; replace auth with the real key.
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+p.cfg.OpenAIKey)
	if org := r.Header.Get("OpenAI-Organization"); org != "" {
		req.Header.Set("OpenAI-Organization", org)
	}
	return req, nil
}

// recordUsage parses the `usage` block from a non-streaming completion and adds
// prompt/completion token counts to metrics. Best-effort; ignores parse errors.
func (p *Proxy) recordUsage(model string, body []byte) {
	var parsed struct {
		Usage struct {
			PromptTokens     float64 `json:"prompt_tokens"`
			CompletionTokens float64 `json:"completion_tokens"`
		} `json:"usage"`
	}
	if json.Unmarshal(body, &parsed) != nil {
		return
	}
	if parsed.Usage.PromptTokens > 0 {
		p.metrics.tokensUsed.WithLabelValues(model, "prompt").Add(parsed.Usage.PromptTokens)
	}
	if parsed.Usage.CompletionTokens > 0 {
		p.metrics.tokensUsed.WithLabelValues(model, "completion").Add(parsed.Usage.CompletionTokens)
	}
}

// finish writes the final response to the caller and records metrics/log.
func (p *Proxy) finish(w http.ResponseWriter, model string, start time.Time, status int, header http.Header, body []byte, kind string) {
	if header != nil {
		copyHeaders(w.Header(), header)
	} else {
		w.Header().Set("Content-Type", "application/json")
	}
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

func copyHeaders(dst, src http.Header) {
	for k, vs := range src {
		// Hop-by-hop headers shouldn't be copied verbatim.
		if k == "Connection" || k == "Transfer-Encoding" || k == "Keep-Alive" {
			continue
		}
		for _, v := range vs {
			dst.Add(k, v)
		}
	}
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
