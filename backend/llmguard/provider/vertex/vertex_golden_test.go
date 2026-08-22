package vertex

// Golden-file tests for the Vertex adapter: the OpenAI->generateContent request,
// the response and the SSE chunks, each compared byte-for-byte against a fixture
// in testdata/.
//
// There is deliberately no -update flag. A regenerable golden turns "the bytes
// changed" into one command that re-blesses whatever the code now does; editing
// a fixture by hand forces the change to be justified in review.
//
// Grew out of vertex_tools_test.go, which hosted this infrastructure alongside
// the tool-translation tests. Those went with tool calling; the fixtures stayed.

import (
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"documedai/llmguard/provider"
)

// goldenCreated is the pinned `created` timestamp for fixtures. generateContent
// returns no timestamp, so the adapter reads a clock; pinning it is what keeps
// the response fixtures byte-stable. Same value as mockupstream.DefaultCreated
// (2025-01-01T00:00:00Z) so fixtures across the repo read alike.
const goldenCreated int64 = 1735689600

// goldenVertex is an adapter with a frozen clock and a counting id source, and
// no token source. It never reaches the network: these tests exercise translation
// only.
//
// Ids are sequential rather than random so fixtures stay byte-stable — the same
// reason the clock is frozen. The counter is per-adapter, so each test that calls
// goldenVertex() starts from 0 and fixtures do not depend on execution order.
func goldenVertex() *Client {
	var n int
	return &Client{
		project:  "test-project",
		location: "us-central1",
		now:      func() time.Time { return time.Unix(goldenCreated, 0).UTC() },
		newID: func() string {
			n++
			return fmt.Sprintf("%016x", n)
		},
	}
}

// realIDVertex is goldenVertex with the production id source, for the tests that
// are ABOUT id generation — uniqueness and prefix — where a pinned counter would
// assert the fake instead of the code.
func realIDVertex() *Client {
	return &Client{
		project:  "test-project",
		location: "us-central1",
		now:      func() time.Time { return time.Unix(goldenCreated, 0).UTC() },
	}
}

// --- golden: OpenAI request -> generateContent ---

func TestGoldenRequestText(t *testing.T) {
	temp := 0.2
	req := &provider.ChatRequest{
		Model: "gemini-2.5-flash",
		Messages: []provider.Message{
			{Role: "system", Content: "You are terse."},
			{Role: "user", Content: "What is the capital of France?"},
			{Role: "assistant", Content: "Paris."},
			{Role: "user", Content: "And of Japan?"},
		},
		Temperature: &temp,
	}
	assertGoldenJSON(t, "request/text.json", toNative(req))
}

// TestGoldenRequestTools is the tool-call golden for the request direction. It
// covers all three translations in one transcript: the tool catalogue, the
// model's call, and the result being handed back.
// --- golden: generateContent -> OpenAI ---

// nativeTextResponse is a documented-shape generateContent reply.
const nativeTextResponse = `{
  "candidates": [{
    "content": {"role": "model", "parts": [{"text": "Tokyo."}]},
    "finishReason": "STOP",
    "index": 0
  }],
  "usageMetadata": {
    "promptTokenCount": 12,
    "candidatesTokenCount": 3,
    "thoughtsTokenCount": 5,
    "totalTokenCount": 20
  },
  "modelVersion": "gemini-2.5-flash-002"
}`

func TestGoldenResponseText(t *testing.T) {
	out, err := goldenVertex().TranslateResponse(http.StatusOK, []byte(nativeTextResponse))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}
	assertGoldenJSON(t, "response/text.json", out)
}

// --- golden: native SSE -> OpenAI chunks ---

// translateStream runs a sequence of native SSE payloads through the adapter and
// returns every chunk produced, in order.
//
// It feeds the adapter exactly what proxy.go feeds it — one `data:` payload,
// already unwrapped — so the fixture records the adapter's real output boundary.
// The `data: ` framing and the trailing `[DONE]` are written by proxy.go's
// serveStreaming and asserted by proxy_streaming_test.go; reconstructing them
// here would pin a copy of the proxy rather than the adapter.
func translateStream(t *testing.T, v *Client, req *provider.ChatRequest, frames []string) []provider.StreamChunk {
	t.Helper()
	var out []provider.StreamChunk
	for _, f := range frames {
		chunks, err := v.TranslateStreamChunk(req, []byte(f))
		if err != nil {
			t.Fatalf("TranslateStreamChunk(%s): %v", f, err)
		}
		out = append(out, chunks...)
	}
	return out
}

func TestGoldenStreamText(t *testing.T) {
	frames := []string{
		`{"candidates":[{"content":{"role":"model","parts":[{"text":"Tok"}]},"index":0}],"modelVersion":"gemini-2.5-flash-002"}`,
		`{"candidates":[{"content":{"role":"model","parts":[{"text":"yo"}]},"index":0}],"modelVersion":"gemini-2.5-flash-002"}`,
		`{"candidates":[{"content":{"role":"model","parts":[{"text":"."}]},"finishReason":"STOP","index":0}],` +
			`"usageMetadata":{"promptTokenCount":12,"candidatesTokenCount":3,"totalTokenCount":15},` +
			`"modelVersion":"gemini-2.5-flash-002"}`,
	}

	chunks := translateStream(t, goldenVertex(), &provider.ChatRequest{Model: "gemini-2.5-flash"}, frames)
	assertGoldenJSON(t, "stream/text.json", chunks)
}

// TestStreamTerminationIsCallerOwned documents where [DONE] comes from. The
// adapter yields nothing for a metadata-only trailing frame; the terminator is
// the proxy's to write.
func TestStreamTerminationIsCallerOwned(t *testing.T) {
	chunks, err := goldenVertex().TranslateStreamChunk(
		&provider.ChatRequest{Model: "gemini-2.5-flash"},
		[]byte(`{"modelVersion":"gemini-2.5-flash-002"}`))
	if err != nil {
		t.Fatalf("TranslateStreamChunk: %v", err)
	}
	if len(chunks) != 0 {
		t.Errorf("chunks = %d, want 0 for a metadata-only frame", len(chunks))
	}
}

func TestTranslateResponsePopulatesIDAndCreated(t *testing.T) {
	v := realIDVertex()
	out, err := v.TranslateResponse(http.StatusOK, []byte(nativeTextResponse))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}
	if !strings.HasPrefix(out.ID, "chatcmpl-") {
		t.Errorf("id = %q, want a chatcmpl- id", out.ID)
	}
	if out.Created != goldenCreated {
		t.Errorf("created = %d, want %d", out.Created, goldenCreated)
	}

	// Unique per response: two translations of the SAME body must differ, or a
	// client tracing by id cannot tell two calls apart.
	again, err := v.TranslateResponse(http.StatusOK, []byte(nativeTextResponse))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}
	if again.ID == out.ID {
		t.Errorf("id repeated across responses: %q", out.ID)
	}
}

// TestTimestampToleratesNilClock guards the zero-value construction that
// internal/gateway's test harness relies on.
func TestTimestampToleratesNilClock(t *testing.T) {
	out, err := (&Client{}).TranslateResponse(http.StatusOK, []byte(nativeTextResponse))
	if err != nil {
		t.Fatalf("TranslateResponse on a zero-value Vertex: %v", err)
	}
	if out.Created <= 0 {
		t.Errorf("created = %d, want a real timestamp", out.Created)
	}
}

// --- regression: text-only behavior is unchanged ---

// TestToNativeEmptyContentStillEmitsPart pins that a plain message with empty
// content still emits a part. An empty parts array is invalid to Vertex, and
// `omitempty` on Text is what makes that part encode as `{}` — which is why the
// tag stays even though Text is now the only field.
func TestToNativeEmptyContentStillEmitsPart(t *testing.T) {
	native := toNative(&provider.ChatRequest{Messages: []provider.Message{{Role: "user", Content: ""}}})
	if len(native.Contents) != 1 {
		t.Fatalf("contents = %d, want 1", len(native.Contents))
	}
	if len(native.Contents[0].Parts) != 1 {
		t.Fatalf("parts = %d, want 1", len(native.Contents[0].Parts))
	}
	if native.Contents[0].Parts[0].Text != "" {
		t.Errorf("text = %q, want empty", native.Contents[0].Parts[0].Text)
	}
}

