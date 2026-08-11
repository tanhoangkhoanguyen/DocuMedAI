package gateway

// Characterization: the buffered (non-streaming) happy path.
// Harness and thresholds live in harness_test.go.

import (
	"net/http"
	"strings"
	"testing"

	"documedai/llmguard/mockupstream"
)

func TestBufferedHappyPath(t *testing.T) {
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
			body: `{"provider":"mock","model":"gemini-2.5-flash","messages":[` +
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

			// Was a pinned QUIRK (empty `id`, zero `created`); closed by issue
			// #57, which made the Vertex adapter synthesize both. The id is
			// derived from the response bytes, so it is stable for a given
			// response rather than unique per request.
			if !strings.HasPrefix(got.ID, "chatcmpl-") {
				t.Errorf("id = %q, want a chatcmpl- id", got.ID)
			}
			if got.Created <= 0 {
				t.Errorf("created = %d, want a real unix timestamp", got.Created)
			}

			if h.up.Hits() != 1 {
				t.Errorf("upstream hits = %d, want 1 (no retry on success)", h.up.Hits())
			}
		})
	}
}
