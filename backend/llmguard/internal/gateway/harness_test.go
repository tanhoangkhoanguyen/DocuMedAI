package gateway

// Shared harness for the LLMGuard characterization tests.
//
// The suite locks in what the proxy does TODAY so the Phase 2 refactor can be
// shown to be behavior-preserving. It is deliberately descriptive, not
// prescriptive: where current behavior looks surprising it is pinned as-is and
// flagged with a QUIRK comment rather than corrected. A test that encodes how
// something *should* work would let a refactor silently change what it *does*.
//
// Everything runs against the in-process mockupstream on a loopback httptest
// server. No provider credentials, no outbound network. Failure modes are
// driven through mockupstream's own knobs (error rate, error status, latency,
// outage window) rather than hand-written responses, so the tests exercise a
// real HTTP round trip.
//
// Thresholds come from the REAL defaults in loadConfig():
//
//	RetryMax          4   (1 initial attempt + 3 retries)
//	CircuitMinReqs   10   (breaker needs 10 observations before it may trip)
//	CircuitFailRatio  0.6
//	RateLimitBurst   60
//
// Only RetryBaseDly/RetryMaxDly are shortened, and only where a test would
// otherwise spend seconds asleep. Those are timing knobs — they change how long
// a retry waits, never how many run or when the breaker trips.
//
// The tests themselves sit beside the source file they exercise —
// retry_test.go, dedup_test.go, ratelimit_test.go, circuitbreaker_test.go — with
// proxy.go's larger surface split by path: proxy_buffered_test.go,
// proxy_streaming_test.go, proxy_errors_test.go, plus usage_test.go for token
// accounting. A new behavior area gets a new <source>_test.go next to its code.

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/redis/go-redis/v9"

	"documedai/llmguard/mockupstream"
	"documedai/llmguard/provider"
	"documedai/llmguard/provider/vertex"
)

// mockUpstream is a mockupstream instance on a loopback server, plus a counter
// of how many requests actually reached it. The count is the load-bearing
// assertion for retry, dedup and breaker: it distinguishes "the proxy returned
// an error" from "the proxy stopped calling upstream".
type mockUpstream struct {
	server *httptest.Server
	mock   *mockupstream.Server
	hits   atomic.Int64

	// intercept, when set, runs INSTEAD of the mock and may delegate to it.
	// Assigned before the first request, read on every one — see
	// newHarnessWithHandler.
	intercept func(mock http.Handler, w http.ResponseWriter, r *http.Request)
}

func newMockUpstream(t *testing.T, cfg mockupstream.Config) *mockUpstream {
	t.Helper()

	mu := &mockUpstream{mock: mockupstream.New(cfg)}
	mu.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Count only provider traffic; /_mock/* control calls are not upstream
		// requests and would corrupt the counts the assertions depend on.
		if !strings.HasPrefix(r.URL.Path, "/_mock/") {
			mu.hits.Add(1)
		}
		if mu.intercept != nil {
			mu.intercept(mu.mock, w, r)
			return
		}
		mu.mock.ServeHTTP(w, r)
	}))
	t.Cleanup(mu.server.Close)
	return mu
}

func (m *mockUpstream) Hits() int64 { return m.hits.Load() }

// mockProvider is the seam that points the proxy at the mock.
//
// The Vertex adapter hardcodes its hostname (region-aiplatform.googleapis.com)
// and there is no upstream-base setting to override — so a provider that builds
// its URL against the mock is the only way to exercise the pipeline offline.
// Response and stream translation delegate to the REAL Vertex adapter, so the
// proxy sees genuine Vertex-shaped payloads; only URL construction is ours.
type mockProvider struct {
	base  string
	inner provider.Provider
}

func (p *mockProvider) Name() string { return "mock" }

func (p *mockProvider) BuildRequest(ctx context.Context, req *provider.ChatRequest) (*http.Request, error) {
	contents := make([]any, 0, len(req.Messages))
	for _, m := range req.Messages {
		role := "user"
		if m.Role == "assistant" {
			role = "model"
		}
		contents = append(contents, map[string]any{
			"role":  role,
			"parts": []any{map[string]any{"text": m.Content}},
		})
	}
	body, err := json.Marshal(map[string]any{"contents": contents})
	if err != nil {
		return nil, err
	}

	method, suffix := "generateContent", ""
	if req.Stream {
		method, suffix = "streamGenerateContent", "?alt=sse"
	}
	url := fmt.Sprintf("%s/v1beta/models/%s:%s%s", p.base, req.Model, method, suffix)
	return http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
}

func (p *mockProvider) TranslateResponse(status int, body []byte) (*provider.ChatResponse, error) {
	return p.inner.TranslateResponse(status, body)
}

func (p *mockProvider) TranslateStreamChunk(req *provider.ChatRequest, raw []byte) ([]provider.StreamChunk, error) {
	return p.inner.TranslateStreamChunk(req, raw)
}

// realDefaults mirrors loadConfig()'s production values. Tests start here and
// override only what they must, so a threshold change in config.go shows up as
// a test failure rather than passing against a stale copy.
func realDefaults() Config {
	return Config{
		RateLimitRPM:     480,
		RateLimitBurst:   60,
		RateWaitMax:      5 * time.Second,
		RetryMax:         4,
		RetryBaseDly:     300 * time.Millisecond,
		RetryMaxDly:      8 * time.Second,
		CircuitMinReqs:   10,
		CircuitFailRatio: 0.6,
		CircuitOpenFor:   20 * time.Second,
		UpstreamTimeout:  120 * time.Second,
		MaxIdleConns:     100,
	}
}

// deadRedis is a client pointed at an address where nothing listens, for the
// tests that assert fail-open behavior — the rate limiter admitting when it
// cannot reach Redis, the breaker sharer refusing to shed on a Redis error.
//
// Both need "Redis is down" rather than "Redis is empty", and neither can get
// that from a live server.
func deadRedis() *redis.Client {
	return redis.NewClient(&redis.Options{
		Addr:        "127.0.0.1:1", // reserved, nothing listens
		DialTimeout: 5 * time.Millisecond,
		MaxRetries:  -1,
	})
}

// offlineLimiter is a rate limiter whose Redis is unreachable.
//
// Acquire fails OPEN on a Redis error (ratelimit.go), so every request is
// admitted — which is what tests that are not about shedding want, without
// requiring a Redis server. Shedding itself is covered separately against a
// real Redis, because only a live bucket can return "no token".
func offlineLimiter() *RateLimiter {
	return newRateLimiter(deadRedis(), 480, 60)
}

type harness struct {
	proxy   *Proxy
	metrics *Metrics
	up      *mockUpstream
}

func newHarness(t *testing.T, cfg Config, mockCfg mockupstream.Config, limiter *RateLimiter) *harness {
	t.Helper()
	return newHarnessWithHandler(t, cfg, mockCfg, limiter, nil)
}

// newHarnessWithHandler is newHarness with a hook in front of the mock, for the
// few tests that need a failure the mock cannot express — a hijacked connection,
// a truncated body. `intercept` receives the mock as an http.Handler and may
// either delegate to it or answer itself. A nil intercept is plain newHarness.
func newHarnessWithHandler(
	t *testing.T, cfg Config, mockCfg mockupstream.Config, limiter *RateLimiter,
	intercept func(mock http.Handler, w http.ResponseWriter, r *http.Request),
) *harness {
	t.Helper()

	up := newMockUpstream(t, mockCfg)
	if intercept != nil {
		up.intercept = intercept
	}
	if limiter == nil {
		limiter = offlineLimiter()
	}

	// The provider registry is global; keep tests serial and reset around each.
	provider.Reset()
	t.Cleanup(provider.Reset)
	provider.Register(&mockProvider{base: up.server.URL, inner: &vertex.Client{}})
	// Routing is allowlist-only now, so the harness must enable the route its
	// tests call. Without this every request 400s before reaching the pipeline.
	provider.SetRoutes([]provider.Route{{Provider: "mock", Model: "gemini-2.5-flash"}})

	m := newMetricsWith(prometheus.NewRegistry())
	p := newProxy(cfg, limiter, newDeduper(), m,
		slog.New(slog.NewTextHandler(io.Discard, nil)))

	return &harness{proxy: p, metrics: m, up: up}
}

// modelLabels builds the label values for a request-scoped metric vector, in the
// order the vector declares them: provider first, then model, then any extra
// dimension the vector carries (tokensUsed's kind).
//
// Tests go through this rather than spelling the values out so that adding a
// label to those vectors is one edit here instead of one per assertion. The
// harness registers a single adapter named "mock", so that is the provider every
// gateway test observes.
func modelLabels(model string, extra ...string) []string {
	return append([]string{"mock", model}, extra...)
}

// chatBody builds a request body for the harness's single registered adapter.
//
// provider is filled in here rather than by each caller: it is required on every
// request, and the harness only ever registers "mock", so spelling it out at each
// call site would repeat one constant across the whole suite.
func chatBody(model, prompt string, stream bool) string {
	req := map[string]any{
		"provider": "mock",
		"model":    model,
		"messages": []any{map[string]any{"role": "user", "content": prompt}},
	}
	if stream {
		req["stream"] = true
	}
	b, err := json.Marshal(req)
	if err != nil {
		panic(err)
	}
	return string(b)
}

// do issues one request through the full ServeHTTP pipeline.
func (h *harness) do(t *testing.T, body string, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		r.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	h.proxy.ServeHTTP(rec, r)
	return rec
}

func decodeChat(t *testing.T, rec *httptest.ResponseRecorder) provider.ChatResponse {
	t.Helper()
	var out provider.ChatResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("response is not a chat completion: %v\nbody: %s", err, rec.Body.String())
	}
	return out
}

func decodeError(t *testing.T, rec *httptest.ResponseRecorder) provider.ErrorEnvelope {
	t.Helper()
	var out provider.ErrorEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("response is not an error envelope: %v\nbody: %s", err, rec.Body.String())
	}
	return out
}
