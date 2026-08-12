package provider

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"testing"

	"golang.org/x/oauth2"
)

func f64(v float64) *float64 { return &v }
func iptr(v int) *int        { return &v }

// staticTokens is a TokenSource that never expires — keeps tests offline.
type staticTokens struct{ tok string }

func (s staticTokens) Token() (*oauth2.Token, error) {
	return &oauth2.Token{AccessToken: s.tok, TokenType: "Bearer"}, nil
}

func testVertex() *Vertex {
	return newVertexWithTokens("proj-1", "us-central1", staticTokens{tok: "test-token"})
}

// --- URL construction ---

func TestEndpoint(t *testing.T) {
	v := testVertex()

	got := v.endpoint("gemini-2.5-flash", false)
	want := "https://us-central1-aiplatform.googleapis.com/v1/projects/proj-1/locations/us-central1/publishers/google/models/gemini-2.5-flash:generateContent"
	if got != want {
		t.Errorf("non-streaming endpoint\n got: %s\nwant: %s", got, want)
	}

	got = v.endpoint("gemini-2.5-flash", true)
	want = "https://us-central1-aiplatform.googleapis.com/v1/projects/proj-1/locations/us-central1/publishers/google/models/gemini-2.5-flash:streamGenerateContent?alt=sse"
	if got != want {
		t.Errorf("streaming endpoint\n got: %s\nwant: %s", got, want)
	}
}

func TestEndpointHonorsRegion(t *testing.T) {
	v := newVertexWithTokens("p", "europe-west4", staticTokens{tok: "t"})
	got := v.endpoint("gemini-2.5-flash", false)
	want := "https://europe-west4-aiplatform.googleapis.com/v1/projects/p/locations/europe-west4/publishers/google/models/gemini-2.5-flash:generateContent"
	if got != want {
		t.Errorf("region must appear in host AND path\n got: %s\nwant: %s", got, want)
	}
}

func TestNewVertexRequiresProject(t *testing.T) {
	if _, err := NewVertex(context.Background(), "vertex-prod", "", "us-central1"); err == nil {
		t.Fatal("expected an error when GOOGLE_CLOUD_PROJECT is empty")
	}
}

// --- request translation ---

func TestToNativeSystemHoisting(t *testing.T) {
	req := &ChatRequest{
		Model: "gemini-2.5-flash",
		Messages: []Message{
			{Role: "system", Content: "You are terse."},
			{Role: "user", Content: "hi"},
			{Role: "assistant", Content: "hello"},
			{Role: "user", Content: "again"},
		},
	}
	got := toNative(req)

	if got.SystemInstruction == nil {
		t.Fatal("system message must be hoisted into systemInstruction")
	}
	if got.SystemInstruction.Parts[0].Text != "You are terse." {
		t.Errorf("systemInstruction = %q", got.SystemInstruction.Parts[0].Text)
	}
	if len(got.Contents) != 3 {
		t.Fatalf("system must not appear in contents; got %d entries", len(got.Contents))
	}
	wantRoles := []string{"user", "model", "user"}
	for i, want := range wantRoles {
		if got.Contents[i].Role != want {
			t.Errorf("contents[%d].role = %q, want %q", i, got.Contents[i].Role, want)
		}
	}
}

func TestToNativeJoinsMultipleSystemMessages(t *testing.T) {
	got := toNative(&ChatRequest{Messages: []Message{
		{Role: "system", Content: "A"},
		{Role: "system", Content: "B"},
		{Role: "user", Content: "q"},
	}})
	if got.SystemInstruction.Parts[0].Text != "A\n\nB" {
		t.Errorf("system join = %q, want %q", got.SystemInstruction.Parts[0].Text, "A\n\nB")
	}
}

func TestToNativeGenerationConfig(t *testing.T) {
	got := toNative(&ChatRequest{
		Messages:    []Message{{Role: "user", Content: "x"}},
		Temperature: f64(0.2),
		MaxTokens:   iptr(256),
		Stop:        []string{"END"},
	})
	if got.GenerationConfig == nil {
		t.Fatal("generationConfig must be set when params are present")
	}
	if *got.GenerationConfig.Temperature != 0.2 {
		t.Errorf("temperature = %v", *got.GenerationConfig.Temperature)
	}
	if *got.GenerationConfig.MaxOutputTokens != 256 {
		t.Errorf("maxOutputTokens = %v", *got.GenerationConfig.MaxOutputTokens)
	}
	if got.GenerationConfig.StopSequences[0] != "END" {
		t.Errorf("stopSequences = %v", got.GenerationConfig.StopSequences)
	}
}

func TestToNativeOmitsEmptyGenerationConfig(t *testing.T) {
	got := toNative(&ChatRequest{Messages: []Message{{Role: "user", Content: "x"}}})
	if got.GenerationConfig != nil {
		t.Error("generationConfig must be omitted when no params are set")
	}
}

// Temperature 0 must survive: it is meaningfully different from unset.
func TestToNativeZeroTemperatureIsKept(t *testing.T) {
	got := toNative(&ChatRequest{
		Messages:    []Message{{Role: "user", Content: "x"}},
		Temperature: f64(0),
	})
	if got.GenerationConfig == nil || got.GenerationConfig.Temperature == nil {
		t.Fatal("temperature=0 must be forwarded, not dropped")
	}
	if *got.GenerationConfig.Temperature != 0 {
		t.Errorf("temperature = %v, want 0", *got.GenerationConfig.Temperature)
	}
}

func TestBuildRequestSetsAuthAndURL(t *testing.T) {
	v := testVertex()
	req, err := v.BuildRequest(context.Background(), &ChatRequest{
		Model:    "gemini-2.5-flash",
		Messages: []Message{{Role: "user", Content: "hi"}},
	})
	if err != nil {
		t.Fatalf("BuildRequest: %v", err)
	}
	if got := req.Header.Get("Authorization"); got != "Bearer test-token" {
		t.Errorf("Authorization = %q, want %q", got, "Bearer test-token")
	}
	if got := req.Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("Content-Type = %q", got)
	}
	if req.Method != http.MethodPost {
		t.Errorf("method = %s", req.Method)
	}
	body, _ := io.ReadAll(req.Body)
	var native vertexRequest
	if err := json.Unmarshal(body, &native); err != nil {
		t.Fatalf("body is not valid vertex JSON: %v", err)
	}
	if len(native.Contents) != 1 || native.Contents[0].Parts[0].Text != "hi" {
		t.Errorf("unexpected body: %s", body)
	}
}

// --- response translation ---

const goldenResponse = `{
  "candidates": [{
    "content": {"role": "model", "parts": [{"text": "Hello there."}]},
    "finishReason": "STOP",
    "index": 0
  }],
  "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 5, "totalTokenCount": 17},
  "modelVersion": "gemini-2.5-flash"
}`

func TestTranslateResponse(t *testing.T) {
	v := testVertex()
	got, err := v.TranslateResponse(200, []byte(goldenResponse))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}
	if got.Object != "chat.completion" {
		t.Errorf("object = %q", got.Object)
	}
	if len(got.Choices) != 1 {
		t.Fatalf("choices = %d, want 1", len(got.Choices))
	}
	if got.Choices[0].Message.Content != "Hello there." {
		t.Errorf("content = %q", got.Choices[0].Message.Content)
	}
	if got.Choices[0].Message.Role != "assistant" {
		t.Errorf("role = %q, want assistant", got.Choices[0].Message.Role)
	}
	if got.Choices[0].FinishReason != "stop" {
		t.Errorf("finish_reason = %q, want stop", got.Choices[0].FinishReason)
	}
	if got.Usage.PromptTokens != 12 || got.Usage.CompletionTokens != 5 || got.Usage.TotalTokens != 17 {
		t.Errorf("usage = %+v", got.Usage)
	}
}

func TestTranslateResponseJoinsParts(t *testing.T) {
	body := `{"candidates":[{"content":{"parts":[{"text":"foo"},{"text":"bar"}]},"finishReason":"STOP"}]}`
	got, err := testVertex().TranslateResponse(200, []byte(body))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}
	if got.Choices[0].Message.Content != "foobar" {
		t.Errorf("content = %q, want foobar", got.Choices[0].Message.Content)
	}
}

func TestFinishReasonMapping(t *testing.T) {
	cases := map[string]string{
		"STOP":               "stop",
		"MAX_TOKENS":         "length",
		"SAFETY":             "content_filter",
		"PROHIBITED_CONTENT": "content_filter",
		"":                   "",
		"SOMETHING_NEW":      "stop",
	}
	for in, want := range cases {
		if got := finishReason(in); got != want {
			t.Errorf("finishReason(%q) = %q, want %q", in, got, want)
		}
	}
}

// thoughtsTokenCount is billed as output but has no OpenAI field, so it folds
// into completion tokens rather than vanishing.
func TestUsageFoldsThoughtsTokens(t *testing.T) {
	got := toUsage(&vertexUsageMetadata{
		PromptTokenCount:     10,
		CandidatesTokenCount: 20,
		ThoughtsTokenCount:   7,
		TotalTokenCount:      37,
	})
	if got.CompletionTokens != 27 {
		t.Errorf("completion = %d, want 27 (20 candidates + 7 thoughts)", got.CompletionTokens)
	}
	if got.TotalTokens != 37 {
		t.Errorf("total = %d, want 37", got.TotalTokens)
	}
}

func TestUsageDerivesTotalWhenAbsent(t *testing.T) {
	got := toUsage(&vertexUsageMetadata{PromptTokenCount: 3, CandidatesTokenCount: 4})
	if got.TotalTokens != 7 {
		t.Errorf("total = %d, want 7", got.TotalTokens)
	}
}

func TestUsageNilIsZero(t *testing.T) {
	if got := toUsage(nil); got != (Usage{}) {
		t.Errorf("toUsage(nil) = %+v, want zero", got)
	}
}

// --- errors ---

func TestTranslateResponseUpstreamError(t *testing.T) {
	body := `{"error":{"code":429,"message":"Quota exceeded","status":"RESOURCE_EXHAUSTED"}}`
	_, err := testVertex().TranslateResponse(429, []byte(body))
	if err == nil {
		t.Fatal("expected an error for status 429")
	}
	ue, ok := err.(*UpstreamError)
	if !ok {
		t.Fatalf("error type = %T, want *UpstreamError", err)
	}
	if ue.Status != 429 {
		t.Errorf("status = %d", ue.Status)
	}
	if ue.Body.Error.Message != "Quota exceeded" {
		t.Errorf("message = %q", ue.Body.Error.Message)
	}
	if ue.Body.Error.Type != "rate_limit" {
		t.Errorf("type = %q, want rate_limit", ue.Body.Error.Type)
	}
}

func TestTranslateResponseNonJSONError(t *testing.T) {
	_, err := testVertex().TranslateResponse(502, []byte("upstream exploded"))
	ue, ok := err.(*UpstreamError)
	if !ok {
		t.Fatalf("error type = %T, want *UpstreamError", err)
	}
	if ue.Body.Error.Message != "upstream exploded" {
		t.Errorf("message = %q", ue.Body.Error.Message)
	}
	if ue.Body.Error.Type != "upstream_error" {
		t.Errorf("type = %q, want upstream_error", ue.Body.Error.Type)
	}
}

func TestErrorTypeMapping(t *testing.T) {
	cases := map[int]string{
		429: "rate_limit",
		401: "auth_error",
		403: "auth_error",
		500: "upstream_error",
		400: "invalid_request_error",
	}
	for status, want := range cases {
		if got := errorType(status); got != want {
			t.Errorf("errorType(%d) = %q, want %q", status, got, want)
		}
	}
}

// --- streaming ---

func TestTranslateStreamChunk(t *testing.T) {
	v := testVertex()
	req := &ChatRequest{Model: "gemini-2.5-flash"}

	raw := `{"candidates":[{"content":{"role":"model","parts":[{"text":"Hel"}]},"index":0}],"modelVersion":"gemini-2.5-flash"}`
	chunks, err := v.TranslateStreamChunk(req, []byte(raw))
	if err != nil {
		t.Fatalf("TranslateStreamChunk: %v", err)
	}
	if len(chunks) != 1 {
		t.Fatalf("chunks = %d, want 1", len(chunks))
	}
	if chunks[0].Object != "chat.completion.chunk" {
		t.Errorf("object = %q", chunks[0].Object)
	}
	if chunks[0].Choices[0].Delta.Content != "Hel" {
		t.Errorf("delta = %q", chunks[0].Choices[0].Delta.Content)
	}
	if chunks[0].Usage != nil {
		t.Error("usage must be attached only on the finishing chunk")
	}
}

func TestTranslateStreamChunkFinalCarriesUsage(t *testing.T) {
	raw := `{"candidates":[{"content":{"parts":[{"text":"!"}]},"finishReason":"STOP"}],
	         "usageMetadata":{"promptTokenCount":4,"candidatesTokenCount":6,"totalTokenCount":10}}`
	chunks, err := testVertex().TranslateStreamChunk(&ChatRequest{Model: "m"}, []byte(raw))
	if err != nil {
		t.Fatalf("TranslateStreamChunk: %v", err)
	}
	if chunks[0].Usage == nil {
		t.Fatal("final chunk must carry usage")
	}
	if chunks[0].Usage.TotalTokens != 10 {
		t.Errorf("total = %d, want 10", chunks[0].Usage.TotalTokens)
	}
	if chunks[0].Choices[0].FinishReason != "stop" {
		t.Errorf("finish_reason = %q", chunks[0].Choices[0].FinishReason)
	}
}

func TestTranslateStreamChunkEmptyIsSkipped(t *testing.T) {
	chunks, err := testVertex().TranslateStreamChunk(&ChatRequest{Model: "m"}, []byte("   "))
	if err != nil {
		t.Fatalf("empty payload should not error: %v", err)
	}
	if len(chunks) != 0 {
		t.Errorf("chunks = %d, want 0", len(chunks))
	}
}

func TestTranslateStreamChunkFallsBackToRequestModel(t *testing.T) {
	raw := `{"candidates":[{"content":{"parts":[{"text":"x"}]}}]}`
	chunks, _ := testVertex().TranslateStreamChunk(&ChatRequest{Model: "gemini-2.5-flash"}, []byte(raw))
	if chunks[0].Model != "gemini-2.5-flash" {
		t.Errorf("model = %q, want the request's model when the frame omits it", chunks[0].Model)
	}
}

// --- registry ---

func TestRegistryRoutesExactly(t *testing.T) {
	Reset()
	defer Reset()

	Register(testVertex())
	SetRoutes([]Route{{Provider: "vertex", Model: "gemini-2.5-flash"}})

	got, err := For(Route{Provider: "vertex", Model: "gemini-2.5-flash"})
	if err != nil {
		t.Fatalf("For: %v", err)
	}
	if got.Name() != "vertex" {
		t.Errorf("provider = %q, want vertex", got.Name())
	}
}

// TestRegistryRoutesPerProvider is why the key is a pair rather than a model:
// one model served by two upstreams must resolve to two different adapters.
//
// Keyed on the model alone these two entries collide and one silently wins,
// sending traffic to an upstream the caller did not ask for.
func TestRegistryRoutesPerProvider(t *testing.T) {
	Reset()
	defer Reset()

	Register(testVertex())
	other, err := NewOpenAICompat("openrouter", "https://openrouter.ai/api/v1", "sk-test")
	if err != nil {
		t.Fatalf("NewOpenAICompat: %v", err)
	}
	Register(other)

	SetRoutes([]Route{
		{Provider: "vertex", Model: "gemini-2.5-flash"},
		{Provider: "openrouter", Model: "gemini-2.5-flash"},
	})

	for _, want := range []string{"vertex", "openrouter"} {
		got, err := For(Route{Provider: want, Model: "gemini-2.5-flash"})
		if err != nil {
			t.Fatalf("For(%s): %v", want, err)
		}
		if got.Name() != want {
			t.Errorf("provider = %q, want %q — the two routes must not collide",
				got.Name(), want)
		}
	}
}

// TestRegistryRejectsUnlistedModel is the allowlist's whole purpose: a model the
// operator did not enable must not reach any provider.
//
// The error is typed so proxy.go can answer 400 for this while still answering
// 500 for a wiring failure — the caller can fix one and not the other.
func TestRegistryRejectsUnlistedModel(t *testing.T) {
	Reset()
	defer Reset()

	Register(testVertex())
	SetRoutes([]Route{{Provider: "vertex", Model: "gemini-2.5-flash"}})

	// A prefix of a listed model, which a prefix-matching registry would serve.
	unlisted := Route{Provider: "vertex", Model: "gemini-2.5-flash-preview"}
	_, err := For(unlisted)
	var unknown *UnknownModelError
	if !errors.As(err, &unknown) {
		t.Fatalf("err = %v (%T), want *UnknownModelError", err, err)
	}
	if unknown.Route != unlisted {
		t.Errorf("Route = %v, want %v", unknown.Route, unlisted)
	}
}

// TestRegistryRejectsListedModelOnWrongProvider pins that BOTH halves are
// checked. Enabling a model on one upstream must not enable it on every other
// upstream the operator happens to have declared.
func TestRegistryRejectsListedModelOnWrongProvider(t *testing.T) {
	Reset()
	defer Reset()

	Register(testVertex())
	other, err := NewOpenAICompat("openrouter", "https://openrouter.ai/api/v1", "sk-test")
	if err != nil {
		t.Fatalf("NewOpenAICompat: %v", err)
	}
	Register(other)

	// Only the vertex route is granted.
	SetRoutes([]Route{{Provider: "vertex", Model: "gemini-2.5-flash"}})

	// Same model, registered provider, but the pair was never allowed.
	_, err = For(Route{Provider: "openrouter", Model: "gemini-2.5-flash"})
	var unknown *UnknownModelError
	if !errors.As(err, &unknown) {
		t.Fatalf("err = %v (%T), want *UnknownModelError", err, err)
	}
}

// TestRegistryUnregisteredProviderIsNotAClientError separates the two failures a
// caller must be able to tell apart: an unlisted model is the client's mistake,
// a route pointing at a provider that never registered is ours.
func TestRegistryUnregisteredProviderIsNotAClientError(t *testing.T) {
	Reset()
	defer Reset()

	SetRoutes([]Route{{Provider: "never-registered", Model: "m"}})

	_, err := For(Route{Provider: "never-registered", Model: "m"})
	if err == nil {
		t.Fatal("expected an error")
	}
	var unknown *UnknownModelError
	if errors.As(err, &unknown) {
		t.Error("a loader bug must not be reported as an unknown model")
	}
}

// TestSetRoutesReplaces pins that routing is a wholesale swap, not an append: a
// model dropped from the config must stop resolving.
func TestSetRoutesReplaces(t *testing.T) {
	Reset()
	defer Reset()

	Register(testVertex())
	SetRoutes([]Route{{Provider: "vertex", Model: "old"}})
	SetRoutes([]Route{{Provider: "vertex", Model: "new"}})

	if _, err := For(Route{Provider: "vertex", Model: "old"}); err == nil {
		t.Error("a model removed from the config must no longer resolve")
	}
	if _, err := For(Route{Provider: "vertex", Model: "new"}); err != nil {
		t.Errorf("For(new): %v", err)
	}
}

func TestEnabledRoutesIsSorted(t *testing.T) {
	Reset()
	defer Reset()

	Register(testVertex())
	SetRoutes([]Route{
		{Provider: "vertex", Model: "zeta"},
		{Provider: "vertex", Model: "alpha"},
		{Provider: "vertex", Model: "mid"},
	})

	// Sorted, because this list is quoted back in a 400 body and map iteration
	// order would make that response differ between identical calls.
	got := EnabledRoutes()
	want := []string{"vertex/alpha", "vertex/mid", "vertex/zeta"}
	if len(got) != len(want) || got[0] != want[0] || got[1] != want[1] || got[2] != want[2] {
		t.Errorf("EnabledRoutes() = %v, want %v", got, want)
	}
}
