package main

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"documedai/llmguard/provider"
)

// fakeProvider stands in for a vendor adapter: it points BuildRequest at a test
// server and reuses Vertex's own frame shape so we exercise the real SSE
// translation path in serveStreaming.
type fakeProvider struct {
	upstream string
	inner    provider.Provider
}

func (f *fakeProvider) Name() string { return "fake" }

func (f *fakeProvider) BuildRequest(ctx context.Context, req *provider.ChatRequest) (*http.Request, error) {
	return http.NewRequestWithContext(ctx, http.MethodPost, f.upstream, strings.NewReader("{}"))
}

func (f *fakeProvider) TranslateResponse(status int, body []byte) (*provider.ChatResponse, error) {
	return f.inner.TranslateResponse(status, body)
}

func (f *fakeProvider) TranslateStreamChunk(req *provider.ChatRequest, raw []byte) ([]provider.StreamChunk, error) {
	return f.inner.TranslateStreamChunk(req, raw)
}

// vertexForTest returns a Vertex adapter used only for its translation methods.
// fakeProvider overrides BuildRequest, so no credentials are involved.
func vertexForTest(t *testing.T) provider.Provider {
	t.Helper()
	return &provider.Vertex{}
}

func testProxy(t *testing.T) *Proxy {
	t.Helper()
	cfg := Config{Provider: "fake", RetryMax: 1, CircuitMinReqs: 100, CircuitFailRatio: 1}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	// A private registry per test: newMetrics() targets the global one and
	// would panic on the second call.
	m := newMetricsWith(prometheus.NewRegistry())
	return newProxy(cfg, nil, newDeduper(), m, log)
}

// TestServeStreamingTranslatesSSE drives the real streaming handler end to end:
// native Vertex frames in, OpenAI chunks + [DONE] out.
func TestServeStreamingTranslatesSSE(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		fl := w.(http.Flusher)
		frames := []string{
			`{"candidates":[{"content":{"role":"model","parts":[{"text":"Hel"}]},"index":0}],"modelVersion":"gemini-2.5-flash"}`,
			`{"candidates":[{"content":{"parts":[{"text":"lo"}]},"index":0}]}`,
			`{"candidates":[{"content":{"parts":[{"text":"!"}]},"finishReason":"STOP","index":0}],` +
				`"usageMetadata":{"promptTokenCount":3,"candidatesTokenCount":4,"totalTokenCount":7}}`,
		}
		for _, f := range frames {
			_, _ = io.WriteString(w, "data: "+f+"\n\n")
			fl.Flush()
		}
	}))
	defer upstream.Close()

	p := testProxy(t)
	prov := &fakeProvider{
		upstream: upstream.URL,
		inner:    vertexForTest(t),
	}

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	chatReq := &provider.ChatRequest{Model: "gemini-2.5-flash", Stream: true}

	p.serveStreaming(rec, req, prov, chatReq, time.Now())

	body := rec.Body.String()

	if ct := rec.Header().Get("Content-Type"); ct != "text/event-stream" {
		t.Errorf("Content-Type = %q, want text/event-stream", ct)
	}
	if !strings.HasSuffix(strings.TrimSpace(body), "data: [DONE]") {
		t.Errorf("stream must terminate with data: [DONE]; got tail %q",
			body[max(0, len(body)-40):])
	}

	// Every non-terminal frame must be a decodable OpenAI chunk.
	var texts []string
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
			texts = append(texts, chunk.Choices[0].Delta.Content)
		}
		if chunk.Usage != nil {
			sawUsage = true
			if chunk.Usage.TotalTokens != 7 {
				t.Errorf("usage total = %d, want 7", chunk.Usage.TotalTokens)
			}
		}
	}

	if got := strings.Join(texts, ""); got != "Hello!" {
		t.Errorf("reassembled stream = %q, want %q", got, "Hello!")
	}
	if !sawUsage {
		t.Error("the finishing chunk must carry usage so streaming tokens are accounted")
	}
}

// TestServeStreamingUpstreamErrorIsTranslated: a non-2xx upstream must not be
// piped through as a bogus event stream.
func TestServeStreamingUpstreamErrorIsTranslated(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusTooManyRequests)
		_, _ = io.WriteString(w, `{"error":{"code":429,"message":"Quota exceeded","status":"RESOURCE_EXHAUSTED"}}`)
	}))
	defer upstream.Close()

	p := testProxy(t)
	prov := &fakeProvider{
		upstream: upstream.URL,
		inner:    vertexForTest(t),
	}

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	p.serveStreaming(rec, req, prov, &provider.ChatRequest{Model: "gemini-2.5-flash", Stream: true}, time.Now())

	if strings.Contains(rec.Body.String(), "data:") {
		t.Errorf("an upstream error must not be emitted as SSE frames; got %q", rec.Body.String())
	}
}
