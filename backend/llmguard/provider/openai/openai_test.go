package openai

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"documedai/llmguard/mockupstream"
	"documedai/llmguard/provider"
)

// newCompat builds an adapter or fails the test. Every case needs one, and a
// constructor error is never the thing under test.
func newCompat(t *testing.T, baseURL, apiKey string) *Client {
	t.Helper()
	p, err := New("openai-compat", baseURL, apiKey)
	if err != nil {
		t.Fatalf("New(%q): %v", baseURL, err)
	}
	return p
}

// wantUpstreamError asserts err is an *provider.UpstreamError and returns it.
//
// It uses errors.As rather than a type assertion because that is how retry.go
// and proxy.go actually inspect this error — a wrapped error would pass here and
// fail there if the test asserted on the concrete type.
func wantUpstreamError(t *testing.T, err error) *provider.UpstreamError {
	t.Helper()
	var ue *provider.UpstreamError
	if !errors.As(err, &ue) {
		t.Fatalf("error = %T (%v), want *UpstreamError", err, err)
	}
	return ue
}

// --- construction ---

func TestNew(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		provName string
		baseURL  string
		wantName string
		wantErr  bool
	}{
		{name: "explicit name", provName: "groq", baseURL: "https://api.groq.com/openai/v1", wantName: "groq"},
		{name: "blank name defaults", provName: "", baseURL: "https://api.openai.com/v1", wantName: "openai-compat"},
		{name: "empty baseURL rejected", provName: "x", baseURL: "", wantErr: true},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			p, err := New(tc.provName, tc.baseURL, "k")
			if tc.wantErr {
				if err == nil {
					t.Fatal("expected an error")
				}
				return
			}
			if err != nil {
				t.Fatalf("New: %v", err)
			}
			if p.Name() != tc.wantName {
				t.Errorf("Name() = %q, want %q", p.Name(), tc.wantName)
			}
		})
	}
}

// --- BuildRequest ---

// TestBuildRequestEndpoint pins the URL for every supported vendor shape. These
// are the only per-vendor differences the adapter has to absorb, so they are the
// whole reason it is config-driven.
func TestBuildRequestEndpoint(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		baseURL string
		want    string
	}{
		{
			name:    "openai",
			baseURL: "https://api.openai.com/v1",
			want:    "https://api.openai.com/v1/chat/completions",
		},
		{
			name:    "openrouter with trailing slash",
			baseURL: "https://openrouter.ai/api/v1/",
			want:    "https://openrouter.ai/api/v1/chat/completions",
		},
		{
			name:    "groq nests v1 under a path",
			baseURL: "https://api.groq.com/openai/v1",
			want:    "https://api.groq.com/openai/v1/chat/completions",
		},
		{
			// Gemini's compat endpoint is NOT under /v1. A hard-coded "/v1"
			// suffix would produce .../v1beta/openai/v1/chat/completions.
			name:    "gemini openai-compat",
			baseURL: "https://generativelanguage.googleapis.com/v1beta/openai",
			want:    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
		},
		{
			name:    "local vllm",
			baseURL: "http://localhost:8000/v1",
			want:    "http://localhost:8000/v1/chat/completions",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			p := newCompat(t, tc.baseURL, "k")
			req, err := p.BuildRequest(context.Background(), &provider.ChatRequest{Model: "m"})
			if err != nil {
				t.Fatalf("BuildRequest: %v", err)
			}
			if got := req.URL.String(); got != tc.want {
				t.Errorf("URL = %q, want %q", got, tc.want)
			}
			if req.Method != http.MethodPost {
				t.Errorf("method = %q, want POST", req.Method)
			}
		})
	}
}

func TestBuildRequestHeaders(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		apiKey   string
		wantAuth string // "" means the header must be absent
	}{
		{name: "key is injected as bearer", apiKey: "sk-secret", wantAuth: "Bearer sk-secret"},
		// A local vLLM accepts no credential; sending "Bearer " is worse than
		// sending nothing.
		{name: "keyless upstream omits auth", apiKey: "", wantAuth: ""},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			p := newCompat(t, "https://api.openai.com/v1", tc.apiKey)
			req, err := p.BuildRequest(context.Background(), &provider.ChatRequest{Model: "m"})
			if err != nil {
				t.Fatalf("BuildRequest: %v", err)
			}
			if got := req.Header.Get("Content-Type"); got != "application/json" {
				t.Errorf("Content-Type = %q, want application/json", got)
			}
			if tc.wantAuth == "" {
				if _, present := req.Header["Authorization"]; present {
					t.Errorf("Authorization present = %q, want absent", req.Header.Get("Authorization"))
				}
				return
			}
			if got := req.Header.Get("Authorization"); got != tc.wantAuth {
				t.Errorf("Authorization = %q, want %q", got, tc.wantAuth)
			}
		})
	}
}

// TestBuildRequestStreamTravelsInBody guards the transport choice: an
// OpenAI-compatible upstream selects streaming from the body, not the URL.
func TestBuildRequestStreamTravelsInBody(t *testing.T) {
	t.Parallel()

	p := newCompat(t, "https://api.openai.com/v1", "k")
	req, err := p.BuildRequest(context.Background(), &provider.ChatRequest{Model: "m", Stream: true})
	if err != nil {
		t.Fatalf("BuildRequest: %v", err)
	}
	body, err := io.ReadAll(req.Body)
	if err != nil {
		t.Fatalf("read body: %v", err)
	}
	if !bytes.Contains(body, []byte(`"stream":true`)) {
		t.Errorf("stream flag missing from body: %s", body)
	}
	if req.URL.RawQuery != "" {
		t.Errorf("query = %q, want empty", req.URL.RawQuery)
	}
}

// TestBuildRequestStripsProvider pins that LLMGuard's own routing field does not
// reach the upstream.
//
// This adapter marshals provider.ChatRequest straight through, so a field added for
// LLMGuard's benefit ships to the vendor by default. OpenAI rejects a body
// carrying an unrecognized field, which would turn every request into a 400 —
// and only against a real upstream, since a permissive mock would accept it.
func TestBuildRequestStripsProvider(t *testing.T) {
	t.Parallel()

	p := newCompat(t, "https://api.openai.com/v1", "k")
	req := &provider.ChatRequest{Provider: "openrouter", Model: "m"}

	httpReq, err := p.BuildRequest(context.Background(), req)
	if err != nil {
		t.Fatalf("BuildRequest: %v", err)
	}
	body, err := io.ReadAll(httpReq.Body)
	if err != nil {
		t.Fatalf("read body: %v", err)
	}
	if bytes.Contains(body, []byte(`"provider"`)) {
		t.Errorf("provider leaked to the upstream body: %s", body)
	}

	// The caller's request must be untouched: the retry loop reuses this struct
	// across attempts, so stripping in place would blank the field that resolved
	// the adapter and make attempt 2 differ from attempt 1.
	if req.Provider != "openrouter" {
		t.Errorf("req.Provider = %q, want it left intact for the retry loop", req.Provider)
	}
}

// --- TranslateResponse ---

// TestTranslateResponsePassesThroughIDAndCreated pins the difference from Vertex.
//
// generateContent sends neither field, so the Vertex adapter ships them empty —
// a pinned quirk. Here they are real upstream values, and OpenAI clients key off
// the response id, so dropping them would break callers rather than merely look
// different. The rest of provider.ChatResponse is plain json.Unmarshal and is not asserted.
func TestTranslateResponsePassesThroughIDAndCreated(t *testing.T) {
	t.Parallel()

	p := newCompat(t, "https://api.openai.com/v1", "k")
	out, err := p.TranslateResponse(http.StatusOK, []byte(`{
	  "id": "chatcmpl-abc123",
	  "object": "chat.completion",
	  "created": 1735689600,
	  "model": "gpt-4o-mini",
	  "choices": [{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}]
	}`))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}
	if out.ID != "chatcmpl-abc123" {
		t.Errorf("id = %q, want chatcmpl-abc123", out.ID)
	}
	if out.Created != 1735689600 {
		t.Errorf("created = %d, want 1735689600", out.Created)
	}
}

func TestTranslateResponseDefaultsObject(t *testing.T) {
	t.Parallel()

	p := newCompat(t, "https://api.openai.com/v1", "k")
	out, err := p.TranslateResponse(http.StatusOK, []byte(`{"id":"x","choices":[]}`))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}
	if out.Object != "chat.completion" {
		t.Errorf("object = %q, want chat.completion", out.Object)
	}
}

func TestTranslateResponseMalformedSuccessBody(t *testing.T) {
	t.Parallel()

	p := newCompat(t, "https://api.openai.com/v1", "k")
	if _, err := p.TranslateResponse(http.StatusOK, []byte("{not json")); err == nil {
		t.Fatal("expected a decode error")
	}
}

// TestTranslateResponseErrors covers the two branches errorEnvelope
// owns. Status→type classification is NOT retested here: that is the shared
// ErrorType(), already pinned by TestErrorTypeMapping in provider_test.go.
func TestTranslateResponseErrors(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name        string
		status      int
		body        string
		wantType    string
		wantMsgPart string
	}{
		{
			// The vendor's own type must NOT be overwritten by ErrorType(status),
			// which would flatten "rate_limit_exceeded" into "rate_limit".
			name:        "vendor type survives",
			status:      http.StatusTooManyRequests,
			body:        `{"error":{"message":"rate limited","type":"rate_limit_exceeded","code":"429"}}`,
			wantType:    "rate_limit_exceeded",
			wantMsgPart: "rate limited",
		},
		{
			// A gateway in front of the provider can answer with HTML or nothing.
			// Swallowing that leaves the caller a status and no explanation.
			name:        "empty body falls back to the status",
			status:      http.StatusInternalServerError,
			body:        "",
			wantType:    "upstream_error",
			wantMsgPart: "500",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			p := newCompat(t, "https://api.openai.com/v1", "k")
			_, err := p.TranslateResponse(tc.status, []byte(tc.body))
			ue := wantUpstreamError(t, err)

			if ue.Status != tc.status {
				t.Errorf("status = %d, want %d", ue.Status, tc.status)
			}
			if ue.Body.Error.Type != tc.wantType {
				t.Errorf("type = %q, want %q", ue.Body.Error.Type, tc.wantType)
			}
			if !strings.Contains(ue.Body.Error.Message, tc.wantMsgPart) {
				t.Errorf("message = %q, want it to contain %q", ue.Body.Error.Message, tc.wantMsgPart)
			}
		})
	}
}

// --- TranslateStreamChunk ---

// TestTranslateStreamChunkSkips pins which frames produce nothing
// client-visible. The usage-only case is the one that matters: OpenAI's final
// usage frame carries an EMPTY choices array, so skipping on "no choices" alone
// would silently lose token accounting on the streaming path.
func TestTranslateStreamChunkSkips(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		raw  string
		want int
	}{
		{name: "done sentinel", raw: "[DONE]", want: 0},
		{name: "done sentinel with prefix", raw: "data: [DONE]", want: 0},
		{name: "empty frame", raw: "", want: 0},
		{name: "whitespace frame", raw: "   ", want: 0},
		{name: "metadata only", raw: `{"id":"c","choices":[]}`, want: 0},
		{name: "usage only is kept", raw: `{"id":"c","choices":[],"usage":{"prompt_tokens":7,` +
			`"completion_tokens":3,"total_tokens":10}}`, want: 1},
		{name: "delta", raw: `{"choices":[{"index":0,"delta":{"content":"x"}}]}`, want: 1},
		{name: "delta with data prefix", raw: `data: {"choices":[{"index":0,"delta":{"content":"x"}}]}`, want: 1},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			p := newCompat(t, "https://api.openai.com/v1", "k")
			chunks, err := p.TranslateStreamChunk(&provider.ChatRequest{Model: "m"}, []byte(tc.raw))
			if err != nil {
				t.Fatalf("TranslateStreamChunk(%q): %v", tc.raw, err)
			}
			if len(chunks) != tc.want {
				t.Errorf("chunks = %d, want %d", len(chunks), tc.want)
			}
		})
	}
}

// TestTranslateStreamChunkFillsDefaults pins the only fields the adapter writes
// itself. An upstream that omits them yields a frame a strict OpenAI client
// rejects, and the model fallback is what keeps per-model metrics attributed.
func TestTranslateStreamChunkFillsDefaults(t *testing.T) {
	t.Parallel()

	p := newCompat(t, "https://api.openai.com/v1", "k")
	chunks, err := p.TranslateStreamChunk(
		&provider.ChatRequest{Model: "gpt-4o-mini"},
		[]byte(`{"choices":[{"index":0,"delta":{"content":"x"}}]}`))
	if err != nil {
		t.Fatalf("TranslateStreamChunk: %v", err)
	}
	if len(chunks) != 1 {
		t.Fatalf("chunks = %d, want 1", len(chunks))
	}
	if chunks[0].Model != "gpt-4o-mini" {
		t.Errorf("model = %q, want the request's model", chunks[0].Model)
	}
	if chunks[0].Object != "chat.completion.chunk" {
		t.Errorf("object = %q", chunks[0].Object)
	}
}

func TestTranslateStreamChunkMalformed(t *testing.T) {
	t.Parallel()

	p := newCompat(t, "https://api.openai.com/v1", "k")
	if _, err := p.TranslateStreamChunk(&provider.ChatRequest{Model: "m"}, []byte("{not json")); err == nil {
		t.Fatal("expected a decode error")
	}
}

// --- end-to-end against the mock upstream ---

// startMock runs the shared mockupstream in-process. Its bytes are identical to
// the standalone binary's, so these tests exercise a real HTTP round trip over a
// loopback socket rather than a hand-written response.
func startMock(t *testing.T, cfg mockupstream.Config) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(mockupstream.New(cfg))
	t.Cleanup(srv.Close)
	return srv
}

// roundTrip drives one request through the adapter and the mock, returning the
// raw upstream response for the caller to translate.
func roundTrip(t *testing.T, srv *httptest.Server, p *Client, req *provider.ChatRequest) *http.Response {
	t.Helper()

	httpReq, err := p.BuildRequest(context.Background(), req)
	if err != nil {
		t.Fatalf("BuildRequest: %v", err)
	}
	resp, err := srv.Client().Do(httpReq)
	if err != nil {
		t.Fatalf("upstream call: %v", err)
	}
	t.Cleanup(func() { _ = resp.Body.Close() })
	return resp
}

func TestRoundTripBuffered(t *testing.T) {
	t.Parallel()

	cfg := mockupstream.DefaultConfig()
	srv := startMock(t, cfg)
	p := newCompat(t, srv.URL+"/v1", "sk-test")

	resp := roundTrip(t, srv, p, &provider.ChatRequest{
		Model:    "gpt-4o-mini",
		Messages: []provider.Message{{Role: "user", Content: "hello there, mock upstream"}},
	})
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("read body: %v", err)
	}

	out, err := p.TranslateResponse(resp.StatusCode, body)
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}

	if out.Model != "gpt-4o-mini" {
		t.Errorf("model = %q", out.Model)
	}
	if len(out.Choices) != 1 || out.Choices[0].Message.Content == "" {
		t.Fatalf("no completion content: %+v", out.Choices)
	}
	if out.Choices[0].Message.Role != "assistant" {
		t.Errorf("role = %q, want assistant", out.Choices[0].Message.Role)
	}
	// The mock builds its canned reply from exactly CompletionTokens words.
	if out.Usage.CompletionTokens != cfg.CompletionTokens {
		t.Errorf("completion_tokens = %d, want %d", out.Usage.CompletionTokens, cfg.CompletionTokens)
	}
	if out.Usage.PromptTokens <= 0 {
		t.Errorf("prompt_tokens = %d, want > 0", out.Usage.PromptTokens)
	}
	if want := out.Usage.PromptTokens + out.Usage.CompletionTokens; out.Usage.TotalTokens != want {
		t.Errorf("total_tokens = %d, want %d", out.Usage.TotalTokens, want)
	}
	if !strings.HasPrefix(out.ID, "chatcmpl-") {
		t.Errorf("id = %q, want a chatcmpl- id", out.ID)
	}
	if out.Created != mockupstream.DefaultCreated {
		t.Errorf("created = %d, want %d", out.Created, mockupstream.DefaultCreated)
	}
}

func TestRoundTripStreaming(t *testing.T) {
	t.Parallel()

	cfg := mockupstream.DefaultConfig()
	srv := startMock(t, cfg)
	p := newCompat(t, srv.URL+"/v1", "sk-test")

	req := &provider.ChatRequest{
		Model:    "gpt-4o-mini",
		Messages: []provider.Message{{Role: "user", Content: "stream to me"}},
		Stream:   true,
	}
	resp := roundTrip(t, srv, p, req)

	if got := resp.Header.Get("Content-Type"); !strings.HasPrefix(got, "text/event-stream") {
		t.Fatalf("Content-Type = %q, want text/event-stream", got)
	}

	// Mirrors proxy.go's scanner, which strips "data:" and [DONE] before calling
	// the adapter — so this drives the adapter the way production does.
	var text strings.Builder
	var translated, sawDone int
	scanner := bufio.NewScanner(resp.Body)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || !strings.HasPrefix(line, "data:") {
			continue
		}
		payload := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
		if payload == "[DONE]" {
			sawDone++
			continue
		}

		chunks, err := p.TranslateStreamChunk(req, []byte(payload))
		if err != nil {
			t.Fatalf("TranslateStreamChunk(%s): %v", payload, err)
		}
		for _, ch := range chunks {
			translated++
			if ch.Object != "chat.completion.chunk" {
				t.Errorf("object = %q", ch.Object)
			}
			if ch.Model != "gpt-4o-mini" {
				t.Errorf("model = %q", ch.Model)
			}
			for _, c := range ch.Choices {
				text.WriteString(c.Delta.Content)
			}
		}
	}
	if err := scanner.Err(); err != nil {
		t.Fatalf("scan stream: %v", err)
	}

	if sawDone != 1 {
		t.Errorf("[DONE] seen %d times, want 1", sawDone)
	}
	if translated == 0 {
		t.Fatal("no chunks translated")
	}
	// Concatenated deltas must reproduce the buffered reply: the mock builds both
	// from the same word slice, so a drift here is a translation bug.
	if got := len(strings.Fields(text.String())); got != cfg.CompletionTokens {
		t.Errorf("assembled %d words, want %d (text=%q)", got, cfg.CompletionTokens, text.String())
	}
}

func TestRoundTripUpstreamFailure(t *testing.T) {
	t.Parallel()

	// error_rate=1.0 makes every request fail, so the error path runs against a
	// real upstream response rather than a hand-written body.
	cfg := mockupstream.DefaultConfig()
	cfg.ErrorRate = 1.0
	srv := startMock(t, cfg)
	p := newCompat(t, srv.URL+"/v1", "sk-test")

	resp := roundTrip(t, srv, p, &provider.ChatRequest{
		Model:    "gpt-4o-mini",
		Messages: []provider.Message{{Role: "user", Content: "hi"}},
	})
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("read body: %v", err)
	}

	_, err = p.TranslateResponse(resp.StatusCode, body)
	ue := wantUpstreamError(t, err)
	if ue.Status != http.StatusServiceUnavailable {
		t.Errorf("status = %d, want 503", ue.Status)
	}
	if ue.Body.Error.Message == "" {
		t.Error("upstream message was lost")
	}
}
