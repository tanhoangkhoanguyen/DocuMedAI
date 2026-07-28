package main

// Characterization tests for the LLMGuard request pipeline.
//
// These lock in what the proxy does TODAY so the Phase 2 refactor can be shown
// to be behavior-preserving. They are deliberately descriptive, not
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
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/redis/go-redis/v9"

	"documedai/llmguard/internal/testutil"
	"documedai/llmguard/mockupstream"
	"documedai/llmguard/provider"
)

// --- harness ----------------------------------------------------------------

// mockUpstream is a mockupstream instance on a loopback server, plus a counter
// of how many requests actually reached it. The count is the load-bearing
// assertion for retry, dedup and breaker: it distinguishes "the proxy returned
// an error" from "the proxy stopped calling upstream".
type mockUpstream struct {
	server *httptest.Server
	mock   *mockupstream.Server
	hits   atomic.Int64
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
		Provider:         "mock",
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

// offlineLimiter is a rate limiter whose Redis is unreachable.
//
// Acquire fails OPEN on a Redis error (ratelimit.go), so every request is
// admitted — which is what tests that are not about shedding want, without
// requiring a Redis server. Shedding itself is covered separately against a
// real Redis, because only a live bucket can return "no token".
func offlineLimiter() *RateLimiter {
	rdb := redis.NewClient(&redis.Options{
		Addr:        "127.0.0.1:1", // reserved, nothing listens
		DialTimeout: 5 * time.Millisecond,
		MaxRetries:  -1,
	})
	return newRateLimiter(rdb, 480, 60)
}

type harness struct {
	proxy   *Proxy
	metrics *Metrics
	up      *mockUpstream
}

func newHarness(t *testing.T, cfg Config, mockCfg mockupstream.Config, limiter *RateLimiter) *harness {
	t.Helper()

	up := newMockUpstream(t, mockCfg)
	if limiter == nil {
		limiter = offlineLimiter()
	}

	// The provider registry is global; keep tests serial and reset around each.
	provider.Reset()
	t.Cleanup(provider.Reset)
	provider.Register(&mockProvider{base: up.server.URL, inner: &provider.Vertex{}})

	m := newMetricsWith(prometheus.NewRegistry())
	p := newProxy(cfg, limiter, newDeduper(), m,
		slog.New(slog.NewTextHandler(io.Discard, nil)))

	return &harness{proxy: p, metrics: m, up: up}
}

func chatBody(model, prompt string, stream bool) string {
	req := map[string]any{
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

// --- 1. happy path, buffered ------------------------------------------------

func TestCharacterizeBufferedHappyPath(t *testing.T) {
	cases := []struct {
		name     string
		body     string
		wantCode int
	}{
		{
			name:     "simple user turn",
			body:     chatBody("gemini-2.5-flash", "summarize the discharge note", false),
			wantCode: http.StatusOK,
		},
		{
			name: "system plus multi-turn",
			body: `{"model":"gemini-2.5-flash","messages":[` +
				`{"role":"system","content":"be terse"},` +
				`{"role":"user","content":"hi"},` +
				`{"role":"assistant","content":"hello"},` +
				`{"role":"user","content":"again"}]}`,
			wantCode: http.StatusOK,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			mcfg := mockupstream.DefaultConfig()
			mcfg.CompletionTokens = 6
			h := newHarness(t, realDefaults(), mcfg, nil)

			rec := h.do(t, tc.body, nil)
			if rec.Code != tc.wantCode {
				t.Fatalf("status = %d, want %d\nbody: %s", rec.Code, tc.wantCode, rec.Body.String())
			}
			if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
				t.Errorf("Content-Type = %q, want application/json", ct)
			}

			got := decodeChat(t, rec)
			if got.Object != "chat.completion" {
				t.Errorf("object = %q, want chat.completion", got.Object)
			}
			if len(got.Choices) != 1 {
				t.Fatalf("choices = %d, want 1", len(got.Choices))
			}
			if got.Choices[0].Message.Role != "assistant" {
				t.Errorf("role = %q, want assistant", got.Choices[0].Message.Role)
			}
			if got.Choices[0].Message.Content == "" {
				t.Error("content must not be empty on the happy path")
			}
			if got.Choices[0].FinishReason != "stop" {
				t.Errorf("finish_reason = %q, want stop", got.Choices[0].FinishReason)
			}

			// QUIRK (pinned, not fixed): the Vertex adapter never populates `id`
			// or `created`, so every buffered completion goes out with the zero
			// values. OpenAI clients that key off response id see "".
			if got.ID != "" {
				t.Errorf("id = %q; today's behavior is an empty id", got.ID)
			}
			if got.Created != 0 {
				t.Errorf("created = %d; today's behavior is 0", got.Created)
			}

			if h.up.Hits() != 1 {
				t.Errorf("upstream hits = %d, want 1 (no retry on success)", h.up.Hits())
			}
		})
	}
}

// --- 2. happy path, streaming -----------------------------------------------

func TestCharacterizeStreamingHappyPath(t *testing.T) {
	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 4
	h := newHarness(t, realDefaults(), mcfg, nil)

	rec := h.do(t, chatBody("gemini-2.5-flash", "stream this", true), nil)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if ct := rec.Header().Get("Content-Type"); ct != "text/event-stream" {
		t.Errorf("Content-Type = %q, want text/event-stream", ct)
	}
	if cc := rec.Header().Get("Cache-Control"); cc != "no-cache" {
		t.Errorf("Cache-Control = %q, want no-cache", cc)
	}

	body := rec.Body.String()
	if !strings.HasSuffix(strings.TrimSpace(body), "data: [DONE]") {
		t.Errorf("stream must end with data: [DONE]; tail = %q", body[max(0, len(body)-40):])
	}

	var text strings.Builder
	var sawUsage bool
	for _, line := range strings.Split(body, "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		payload := strings.TrimPrefix(line, "data: ")
		if payload == "[DONE]" {
			continue
		}
		var chunk provider.StreamChunk
		if err := json.Unmarshal([]byte(payload), &chunk); err != nil {
			t.Fatalf("emitted a non-JSON chunk %q: %v", payload, err)
		}
		if chunk.Object != "chat.completion.chunk" {
			t.Errorf("object = %q, want chat.completion.chunk", chunk.Object)
		}
		if len(chunk.Choices) > 0 {
			text.WriteString(chunk.Choices[0].Delta.Content)
		}
		if chunk.Usage != nil {
			sawUsage = true
		}
	}

	if text.Len() == 0 {
		t.Error("reassembled stream is empty")
	}
	if !sawUsage {
		t.Error("the finishing chunk must carry usage")
	}

	// Streaming takes the no-retry, no-dedup path: exactly one upstream call.
	if h.up.Hits() != 1 {
		t.Errorf("upstream hits = %d, want 1", h.up.Hits())
	}
}

// QUIRK (pinned, not fixed): when the upstream fails on the STREAMING path the
// proxy logs and returns without ever writing a status or body. The client sees
// HTTP 200 with a completely empty payload — no error envelope, no SSE frames.
// The buffered path, by contrast, returns a proper error envelope.
func TestCharacterizeStreamingUpstreamErrorIsSilent(t *testing.T) {
	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusInternalServerError
	h := newHarness(t, realDefaults(), mcfg, nil)

	rec := h.do(t, chatBody("gemini-2.5-flash", "stream this", true), nil)

	if rec.Code != http.StatusOK {
		t.Errorf("status = %d; today's behavior is 200 even though upstream failed", rec.Code)
	}
	if rec.Body.Len() != 0 {
		t.Errorf("body = %q; today's behavior is an empty body", rec.Body.String())
	}
	if strings.Contains(rec.Body.String(), "data:") {
		t.Error("an upstream error must not be emitted as SSE frames")
	}
	// No retry on the streaming path: one attempt only, even for a 500.
	if h.up.Hits() != 1 {
		t.Errorf("upstream hits = %d, want 1 (streaming does not retry)", h.up.Hits())
	}
}

// --- 3. rate-limit shedding -------------------------------------------------

// Needs a real Redis: shedding requires a live token bucket that can return
// "no token". With Redis unreachable the limiter fails OPEN and never sheds,
// so this path cannot be reached offline. Skips when Redis is absent; CI
// provides one.
func TestCharacterizeRateLimitShedding(t *testing.T) {
	rdb := testutil.RequireRedis(t)

	cfg := realDefaults()
	// A bucket of exactly one token, refilling at 1/s, and a short wait so the
	// test does not sit for the production 5s.
	cfg.RateLimitRPM = 60
	cfg.RateLimitBurst = 1
	cfg.RateWaitMax = 150 * time.Millisecond

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 3
	h := newHarness(t, cfg, mcfg, newRateLimiter(rdb, cfg.RateLimitRPM, cfg.RateLimitBurst))

	// The bucket is keyed on (api-key hint + model); a unique key keeps this run
	// independent of anything already in the DB.
	headers := map[string]string{"Authorization": "Bearer sk-test-" + t.Name()}
	body := chatBody("gemini-2.5-flash", "rate limit me", false)

	first := h.do(t, body, headers)
	if first.Code != http.StatusOK {
		t.Fatalf("first request status = %d, want 200 (bucket starts full)\nbody: %s",
			first.Code, first.Body.String())
	}

	second := h.do(t, body, headers)
	if second.Code != http.StatusTooManyRequests {
		t.Fatalf("second request status = %d, want 429 (bucket exhausted)\nbody: %s",
			second.Code, second.Body.String())
	}

	env := decodeError(t, second)
	if env.Error.Type != "rate_limit" {
		t.Errorf("error.type = %q, want rate_limit", env.Error.Type)
	}
	if env.Error.Message != "proxy rate limit exceeded" {
		t.Errorf("error.message = %q", env.Error.Message)
	}

	// Shedding happens BEFORE the provider is called: the shed request must not
	// have reached upstream.
	if h.up.Hits() != 1 {
		t.Errorf("upstream hits = %d, want 1 (the 429 is shed before dispatch)", h.up.Hits())
	}
	if got := testutil.LabeledCounterValue(t, h.metrics.rateLimited, "gemini-2.5-flash"); got != 1 {
		t.Errorf("rateLimited metric = %v, want 1", got)
	}
}

// --- 4. retry -----------------------------------------------------------------

// Pins RetryMax=4: a permanently failing upstream is called exactly four times
// (1 initial + 3 retries), then the vendor's error is surfaced to the caller.
func TestCharacterizeRetryExhaustsAtRetryMax(t *testing.T) {
	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond // timing only; attempt COUNT is the real 4
	cfg.RetryMaxDly = 5 * time.Millisecond

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusInternalServerError
	h := newHarness(t, cfg, mcfg, nil)

	rec := h.do(t, chatBody("gemini-2.5-flash", "always fails", false), nil)

	if got := h.up.Hits(); got != 4 {
		t.Errorf("upstream hits = %d, want 4 (RetryMax)", got)
	}
	// The upstream's own status and message are passed through, NOT masked as 503.
	if rec.Code != http.StatusInternalServerError {
		t.Errorf("status = %d, want 500 (upstream status is surfaced)", rec.Code)
	}
	env := decodeError(t, rec)
	if !strings.Contains(env.Error.Message, "injected") {
		t.Errorf("error.message = %q, want the upstream's own message", env.Error.Message)
	}
	if env.Error.Type != "upstream_error" {
		t.Errorf("error.type = %q, want upstream_error", env.Error.Type)
	}
	// retries metric counts attempts beyond the first.
	if got := testutil.LabeledCounterValue(t, h.metrics.retries, "gemini-2.5-flash"); got != 3 {
		t.Errorf("retries metric = %v, want 3 (RetryMax-1)", got)
	}
}

// Retry-then-succeed, driven by mockupstream's outage window: the first
// attempts land inside the outage and fail, a later one lands after it and
// succeeds. Retries are NOT a fresh request as far as the mock is concerned —
// an identical body yields an identical verdict — so a time-boxed outage is the
// mechanism that makes a retry observably different from its predecessor.
func TestCharacterizeRetryThenSucceed(t *testing.T) {
	cfg := realDefaults()
	// Attempts land at roughly 0ms, 200-250ms, 600-750ms, 1400-1750ms.
	cfg.RetryBaseDly = 200 * time.Millisecond
	cfg.RetryMaxDly = 2 * time.Second

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 3
	h := newHarness(t, cfg, mcfg, nil)

	// A 1s outage covers the first three attempts with ~400ms of margin before
	// the fourth.
	h.up.mock.StartOutage(time.Second)

	start := time.Now()
	rec := h.do(t, chatBody("gemini-2.5-flash", "recovers", false), nil)
	elapsed := time.Since(start)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 (a later retry should land after the outage)\n"+
			"elapsed=%s hits=%d body: %s", rec.Code, elapsed, h.up.Hits(), rec.Body.String())
	}
	got := decodeChat(t, rec)
	if len(got.Choices) != 1 || got.Choices[0].Message.Content == "" {
		t.Errorf("expected a real completion after recovery, got %+v", got)
	}
	if h.up.Hits() < 2 {
		t.Errorf("upstream hits = %d, want at least 2 (failure then success)", h.up.Hits())
	}
	if h.up.Hits() > 4 {
		t.Errorf("upstream hits = %d, must never exceed RetryMax=4", h.up.Hits())
	}
	if got := testutil.LabeledCounterValue(t, h.metrics.retries, "gemini-2.5-flash"); got < 1 {
		t.Errorf("retries metric = %v, want at least 1", got)
	}
}

// --- 5. circuit breaker -------------------------------------------------------

// Pins CircuitMinReqs=10 and CircuitFailRatio=0.6. The breaker wraps the WHOLE
// retry loop, so one client request is ONE breaker observation regardless of how
// many upstream attempts it burns.
func TestCharacterizeCircuitBreakerTrips(t *testing.T) {
	cfg := realDefaults()
	cfg.RetryBaseDly = time.Millisecond // timing only
	cfg.RetryMaxDly = 5 * time.Millisecond

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusInternalServerError
	h := newHarness(t, cfg, mcfg, nil)

	body := chatBody("gemini-2.5-flash", "sustained failure", false)

	// Sequential, so each request is its own singleflight flight and its own
	// breaker observation. Concurrent identical requests would coalesce.
	for i := 1; i <= 10; i++ {
		rec := h.do(t, body, nil)
		if rec.Code != http.StatusInternalServerError {
			t.Fatalf("request %d: status = %d, want 500 while the breaker is still closed", i, rec.Code)
		}
	}

	// 10 requests x 4 attempts, all reaching upstream.
	hitsBeforeTrip := h.up.Hits()
	if hitsBeforeTrip != 40 {
		t.Errorf("upstream hits before trip = %d, want 40 (10 requests x RetryMax 4)", hitsBeforeTrip)
	}

	// The 11th observation finds the breaker open.
	rec := h.do(t, body, nil)
	if rec.Code != http.StatusServiceUnavailable {
		t.Errorf("status after trip = %d, want 503", rec.Code)
	}
	env := decodeError(t, rec)
	if env.Error.Message != "upstream unavailable" {
		t.Errorf("error.message = %q, want %q (breaker-open is NOT the vendor message)",
			env.Error.Message, "upstream unavailable")
	}
	if env.Error.Type != "upstream_error" {
		t.Errorf("error.type = %q, want upstream_error", env.Error.Type)
	}

	// The whole point: an open breaker stops calling upstream entirely.
	if got := h.up.Hits(); got != hitsBeforeTrip {
		t.Errorf("upstream hits = %d, want %d — an open breaker must not dispatch",
			got, hitsBeforeTrip)
	}
	if got := testutil.CounterValue(t, h.metrics.circuitState); got != 2 {
		t.Errorf("circuitState gauge = %v, want 2 (open)", got)
	}
}

// --- 6. dedup -----------------------------------------------------------------

// Identical concurrent requests coalesce into ONE upstream call and all callers
// receive the same response.
func TestCharacterizeDedupCoalescesConcurrentIdenticalRequests(t *testing.T) {
	const callers = 8

	cfg := realDefaults()

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 5
	// Hold the in-flight request open long enough that every caller arrives
	// while the leader is still waiting, which is what singleflight coalesces.
	mcfg.Latency = 400 * time.Millisecond
	h := newHarness(t, cfg, mcfg, nil)

	body := chatBody("gemini-2.5-flash", "coalesce me", false)

	var wg sync.WaitGroup
	bodies := make([]string, callers)
	codes := make([]int, callers)
	for i := range callers {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			rec := h.do(t, body, nil)
			codes[idx] = rec.Code
			bodies[idx] = rec.Body.String()
		}(i)
	}
	wg.Wait()

	if got := h.up.Hits(); got != 1 {
		t.Errorf("upstream hits = %d, want 1 — %d identical concurrent requests must coalesce",
			got, callers)
	}
	for i := range callers {
		if codes[i] != http.StatusOK {
			t.Errorf("caller %d: status = %d, want 200", i, codes[i])
		}
		if bodies[i] != bodies[0] {
			t.Errorf("caller %d got a different body than caller 0 — a shared flight must "+
				"hand back the same response", i)
		}
	}

	// QUIRK (pinned, not fixed): singleflight reports shared=true to EVERY
	// participant including the leader that did the work, so the dedup-hit
	// counter records all 8 callers rather than the 7 that piggy-backed.
	if got := testutil.CounterValue(t, h.metrics.dedupHits); got != float64(callers) {
		t.Errorf("dedupHits = %v, want %d (today the leader is counted too)", got, callers)
	}
}

// A different body is a different dedup key, so nothing coalesces.
func TestCharacterizeDedupDoesNotCoalesceDifferentBodies(t *testing.T) {
	const callers = 4

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = 3
	mcfg.Latency = 200 * time.Millisecond
	h := newHarness(t, realDefaults(), mcfg, nil)

	var wg sync.WaitGroup
	for i := range callers {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			h.do(t, chatBody("gemini-2.5-flash", fmt.Sprintf("distinct prompt %d", idx), false), nil)
		}(i)
	}
	wg.Wait()

	if got := h.up.Hits(); got != callers {
		t.Errorf("upstream hits = %d, want %d — distinct bodies must not coalesce", got, callers)
	}
	if got := testutil.CounterValue(t, h.metrics.dedupHits); got != 0 {
		t.Errorf("dedupHits = %v, want 0", got)
	}
}

// --- 7. usage-token extraction ------------------------------------------------

func TestCharacterizeUsageTokenExtraction(t *testing.T) {
	const completionTokens = 9

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = completionTokens
	h := newHarness(t, realDefaults(), mcfg, nil)

	rec := h.do(t, chatBody("gemini-2.5-flash", "count my tokens please", false), nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}

	got := decodeChat(t, rec)
	if got.Usage.CompletionTokens != completionTokens {
		t.Errorf("completion_tokens = %d, want %d", got.Usage.CompletionTokens, completionTokens)
	}
	if got.Usage.PromptTokens <= 0 {
		t.Errorf("prompt_tokens = %d, want > 0", got.Usage.PromptTokens)
	}
	if got.Usage.TotalTokens != got.Usage.PromptTokens+got.Usage.CompletionTokens {
		t.Errorf("total_tokens = %d, want prompt+completion = %d",
			got.Usage.TotalTokens, got.Usage.PromptTokens+got.Usage.CompletionTokens)
	}

	// The same counts must reach Prometheus, not just the response body.
	if v := testutil.LabeledCounterValue(t, h.metrics.tokensUsed, "gemini-2.5-flash", "completion"); v != completionTokens {
		t.Errorf("tokensUsed{completion} = %v, want %d", v, completionTokens)
	}
	if v := testutil.LabeledCounterValue(t, h.metrics.tokensUsed, "gemini-2.5-flash", "prompt"); v != float64(got.Usage.PromptTokens) {
		t.Errorf("tokensUsed{prompt} = %v, want %d", v, got.Usage.PromptTokens)
	}
}

// Usage is also accounted on the streaming path, from the finishing chunk.
func TestCharacterizeUsageTokenExtractionStreaming(t *testing.T) {
	const completionTokens = 7

	mcfg := mockupstream.DefaultConfig()
	mcfg.CompletionTokens = completionTokens
	h := newHarness(t, realDefaults(), mcfg, nil)

	rec := h.do(t, chatBody("gemini-2.5-flash", "stream my tokens", true), nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}

	if v := testutil.LabeledCounterValue(t, h.metrics.tokensUsed, "gemini-2.5-flash", "completion"); v != completionTokens {
		t.Errorf("tokensUsed{completion} = %v, want %d", v, completionTokens)
	}
	if v := testutil.LabeledCounterValue(t, h.metrics.tokensUsed, "gemini-2.5-flash", "prompt"); v <= 0 {
		t.Errorf("tokensUsed{prompt} = %v, want > 0", v)
	}
}

// --- request validation (the guards in front of the pipeline) -----------------

func TestCharacterizeRequestValidation(t *testing.T) {
	cases := []struct {
		name     string
		method   string
		body     string
		wantCode int
		wantMsg  string
	}{
		{
			name:     "GET is rejected",
			method:   http.MethodGet,
			body:     "",
			wantCode: http.StatusMethodNotAllowed,
			wantMsg:  "method not allowed",
		},
		{
			name:     "malformed JSON",
			method:   http.MethodPost,
			body:     "{not json",
			wantCode: http.StatusBadRequest,
			wantMsg:  "invalid JSON body",
		},
		{
			name:     "missing model",
			method:   http.MethodPost,
			body:     `{"messages":[{"role":"user","content":"hi"}]}`,
			wantCode: http.StatusBadRequest,
			wantMsg:  "field 'model' is required",
		},
		{
			name:     "empty messages",
			method:   http.MethodPost,
			body:     `{"model":"gemini-2.5-flash","messages":[]}`,
			wantCode: http.StatusBadRequest,
			wantMsg:  "field 'messages' must not be empty",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := newHarness(t, realDefaults(), mockupstream.DefaultConfig(), nil)

			r := httptest.NewRequest(tc.method, "/v1/chat/completions", strings.NewReader(tc.body))
			rec := httptest.NewRecorder()
			h.proxy.ServeHTTP(rec, r)

			if rec.Code != tc.wantCode {
				t.Fatalf("status = %d, want %d\nbody: %s", rec.Code, tc.wantCode, rec.Body.String())
			}
			env := decodeError(t, rec)
			if env.Error.Message != tc.wantMsg {
				t.Errorf("error.message = %q, want %q", env.Error.Message, tc.wantMsg)
			}
			if env.Error.Type != "invalid_request_error" {
				t.Errorf("error.type = %q, want invalid_request_error", env.Error.Type)
			}
			// Nothing invalid should ever reach the provider.
			if h.up.Hits() != 0 {
				t.Errorf("upstream hits = %d, want 0 — validation runs before dispatch", h.up.Hits())
			}
		})
	}
}

// --- upstream error passthrough ----------------------------------------------

// The buffered path surfaces the upstream's own status and message rather than
// flattening everything to 503.
//
// QUIRK (pinned, not fixed — this is a real defect, see the report): EVERY
// non-2xx is retried the full RetryMax times, including client errors that
// isRetryable() classifies as not worth retrying. The early-exit in retry.go:93
// is `err == nil && !isRetryable(res.status)`, but TranslateResponse returns an
// *UpstreamError for any non-2xx, so err is never nil on a failure and the
// isRetryable() check is unreachable. A 400 or 401 therefore costs 4 upstream
// calls. The comment on that line ("or a non-retryable client error like
// 400/401") describes the pre-Vertex behavior, when forwardBuffered returned
// (result, nil) for every status.
//
// Phase 2 will likely fix this. These expectations must then be updated
// deliberately — that is the signal, not a broken test.
func TestCharacterizeUpstreamErrorPassthrough(t *testing.T) {
	cases := []struct {
		name       string
		mockStatus int
		wantCode   int
		wantType   string
		wantHits   int64
	}{
		{name: "429 is retried then surfaced", mockStatus: http.StatusTooManyRequests,
			wantCode: http.StatusTooManyRequests, wantType: "rate_limit", wantHits: 4},
		{name: "500 is retried then surfaced", mockStatus: http.StatusInternalServerError,
			wantCode: http.StatusInternalServerError, wantType: "upstream_error", wantHits: 4},
		{name: "503 is retried then surfaced", mockStatus: http.StatusServiceUnavailable,
			wantCode: http.StatusServiceUnavailable, wantType: "upstream_error", wantHits: 4},
		// Retried despite being a client error — see the QUIRK above.
		{name: "400 is retried even though it is not retryable", mockStatus: http.StatusBadRequest,
			wantCode: http.StatusBadRequest, wantType: "invalid_request_error", wantHits: 4},
		{name: "401 is retried even though it is not retryable", mockStatus: http.StatusUnauthorized,
			wantCode: http.StatusUnauthorized, wantType: "auth_error", wantHits: 4},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := realDefaults()
			cfg.RetryBaseDly = time.Millisecond
			cfg.RetryMaxDly = 5 * time.Millisecond

			mcfg := mockupstream.DefaultConfig()
			mcfg.ErrorRate = 1.0
			mcfg.ErrorStatus = tc.mockStatus
			h := newHarness(t, cfg, mcfg, nil)

			rec := h.do(t, chatBody("gemini-2.5-flash", "fail please", false), nil)

			if rec.Code != tc.wantCode {
				t.Errorf("status = %d, want %d\nbody: %s", rec.Code, tc.wantCode, rec.Body.String())
			}
			if got := decodeError(t, rec).Error.Type; got != tc.wantType {
				t.Errorf("error.type = %q, want %q", got, tc.wantType)
			}
			if got := h.up.Hits(); got != tc.wantHits {
				t.Errorf("upstream hits = %d, want %d", got, tc.wantHits)
			}
		})
	}
}

// Retry-After from the upstream is honored in place of exponential backoff.
func TestCharacterizeRetryAfterIsHonored(t *testing.T) {
	cfg := realDefaults()
	// Backoff would be ~1ms per gap; Retry-After: 1 should dominate, making the
	// whole call take at least a second.
	cfg.RetryBaseDly = time.Millisecond
	cfg.RetryMaxDly = 2 * time.Millisecond
	cfg.RetryMax = 2 // one gap, so the test waits ~1s rather than ~3s

	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusTooManyRequests
	mcfg.RetryAfter = 1 // seconds
	h := newHarness(t, cfg, mcfg, nil)

	start := time.Now()
	rec := h.do(t, chatBody("gemini-2.5-flash", "slow down", false), nil)
	elapsed := time.Since(start)

	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want 429", rec.Code)
	}
	if h.up.Hits() != 2 {
		t.Errorf("upstream hits = %d, want 2", h.up.Hits())
	}
	if elapsed < time.Second {
		t.Errorf("elapsed = %s; Retry-After: 1 must override the ~1ms backoff", elapsed)
	}
}
