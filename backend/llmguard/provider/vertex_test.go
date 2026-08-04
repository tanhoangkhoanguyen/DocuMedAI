package provider

import (
	"context"
	"encoding/json"
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
	if _, err := NewVertex(context.Background(), "", "us-central1"); err == nil {
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

func TestRegistryRoutesByPrefix(t *testing.T) {
	Reset()
	defer Reset()

	v := testVertex()
	Register(v)
	RouteModel("gemini-", "vertex")

	got, err := For("gemini-2.5-flash", "vertex")
	if err != nil {
		t.Fatalf("For: %v", err)
	}
	if got.Name() != "vertex" {
		t.Errorf("provider = %q, want vertex", got.Name())
	}
}

func TestRegistryFallsBackToDefault(t *testing.T) {
	Reset()
	defer Reset()

	Register(testVertex())
	got, err := For("some-unknown-model", "vertex")
	if err != nil {
		t.Fatalf("an unrouted model should fall back to the default: %v", err)
	}
	if got.Name() != "vertex" {
		t.Errorf("provider = %q", got.Name())
	}
}

func TestRegistryErrorsWithoutDefault(t *testing.T) {
	Reset()
	defer Reset()

	if _, err := For("anything", "nonexistent"); err == nil {
		t.Fatal("expected an error when neither a rule nor the default resolves")
	}
}

func TestRegistryLongestPrefixWins(t *testing.T) {
	Reset()
	defer Reset()

	Register(testVertex())
	Register(stubProvider{name: "special"})
	RouteModel("gemini-", "vertex")
	RouteModel("gemini-2.5-pro", "special")

	got, _ := For("gemini-2.5-pro", "vertex")
	if got.Name() != "special" {
		t.Errorf("provider = %q, want special (longer prefix must win)", got.Name())
	}
}

type stubProvider struct{ name string }

func (s stubProvider) Name() string { return s.name }
func (s stubProvider) BuildRequest(context.Context, *ChatRequest) (*http.Request, error) {
	return nil, nil
}
func (s stubProvider) TranslateResponse(int, []byte) (*ChatResponse, error) { return nil, nil }
func (s stubProvider) TranslateStreamChunk(*ChatRequest, []byte) ([]StreamChunk, error) {
	return nil, nil
}
