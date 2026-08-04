package gateway

// Characterization: the SSE streaming path — happy path and its silent-failure
// quirk. Harness and thresholds live in harness_test.go.

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"documedai/llmguard/mockupstream"
	"documedai/llmguard/provider"
)

func TestStreamingHappyPath(t *testing.T) {
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
func TestStreamingUpstreamErrorIsSilent(t *testing.T) {
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
