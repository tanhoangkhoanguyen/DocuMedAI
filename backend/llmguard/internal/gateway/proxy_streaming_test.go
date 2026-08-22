package gateway

// Characterization: the SSE streaming path — happy path and its silent-failure
// quirk. Harness and thresholds live in harness_test.go.

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"documedai/llmguard/internal/testutil"
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

	// Streaming takes the no-retry path: exactly one upstream call.
	if h.up.Hits() != 1 {
		t.Errorf("upstream hits = %d, want 1", h.up.Hits())
	}
}

// A streaming failure BEFORE any byte reaches the client returns a real error
// envelope, like the buffered path.
//
// WAS A QUIRK, NOW FIXED: the proxy logged and fell through without writing a
// status or a body, so the client saw HTTP 200 with an empty payload —
// indistinguishable from a successful empty completion. Nothing had been
// streamed at that point, so there was no partial answer to protect: the error
// envelope was always available, just never sent.
func TestStreamingUpstreamErrorReturnsEnvelope(t *testing.T) {
	mcfg := mockupstream.DefaultConfig()
	mcfg.ErrorRate = 1.0
	mcfg.ErrorStatus = http.StatusInternalServerError
	h := newHarness(t, realDefaults(), mcfg, nil)

	rec := h.do(t, chatBody("gemini-2.5-flash", "stream this", true), nil)

	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500 — the vendor's own status\nbody: %s",
			rec.Code, rec.Body.String())
	}
	if got := decodeError(t, rec).Error.Type; got != "upstream_error" {
		t.Errorf("error.type = %q, want upstream_error", got)
	}
	// A pre-header failure takes the JSON path, not the SSE path.
	if strings.Contains(rec.Body.String(), "data:") {
		t.Error("an upstream error must not be emitted as SSE frames when nothing streamed")
	}
	// No retry on the streaming path: one attempt only, even for a 500.
	if h.up.Hits() != 1 {
		t.Errorf("upstream hits = %d, want 1 (streaming does not retry)", h.up.Hits())
	}
	// writeError routes through writeJSON, which records the request itself. The
	// tail of serveStreaming records latency unconditionally, so a missing return
	// in the pre-header branch would observe this request twice — invisible to
	// every status assertion above.
	if got := testutil.HistogramCount(t, h.metrics.latency, modelLabels("gemini-2.5-flash")...); got != 1 {
		t.Errorf("latency observations = %d, want 1 — a failed request must be counted once", got)
	}
}

// A streaming failure AFTER frames are on the wire keeps every delivered frame
// and appends an error frame plus [DONE].
//
// Once WriteHeader(200) has gone out the status is locked in, and the text the
// client already received is a real partial answer that must survive. So the
// failure is reported IN BAND: the client keeps its text and still learns the
// stream ended badly, rather than seeing a truncation it cannot tell apart from
// a clean end.
//
// The mock cannot express "200, valid frames, then a broken body" — that is what
// the intercept hook is for.
func TestStreamingFailureAfterHeaderAppendsErrorFrame(t *testing.T) {
	const delivered = "hello world"

	h := newHarnessWithHandler(t, realDefaults(), mockupstream.DefaultConfig(), nil,
		func(_ http.Handler, w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "text/event-stream")
			w.WriteHeader(http.StatusOK)
			flusher, ok := w.(http.Flusher)
			if !ok {
				return
			}
			// Two well-formed Vertex SSE frames, flushed so the proxy translates
			// and forwards them before the stream breaks.
			for _, part := range []string{"hello ", "world"} {
				frame, err := json.Marshal(map[string]any{
					"candidates": []any{map[string]any{
						"content": map[string]any{
							"role":  "model",
							"parts": []any{map[string]any{"text": part}},
						},
					}},
				})
				if err != nil {
					return
				}
				_, _ = w.Write([]byte("data: "))
				_, _ = w.Write(frame)
				_, _ = w.Write([]byte("\n\n"))
				flusher.Flush()
			}
			// A line longer than the scanner's 1 MiB limit trips scanner.Err()
			// mid-stream, which is the reachable post-header failure path.
			_, _ = w.Write([]byte("data: "))
			_, _ = w.Write([]byte(strings.Repeat("x", 2*1024*1024)))
			flusher.Flush()
		})

	rec := h.do(t, chatBody("gemini-2.5-flash", "stream this", true), nil)

	// 1. The status was already committed and must not be rewritten.
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 — the header was already sent", rec.Code)
	}

	body := rec.Body.String()

	// 2. The constraint: text delivered before the failure is still intact. A fix
	//    that discards partial output to send a clean error passes a naive
	//    "did we report the error" check but fails here.
	var text strings.Builder
	var sawError bool
	for _, line := range strings.Split(body, "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		payload := strings.TrimPrefix(line, "data: ")
		if payload == "[DONE]" {
			continue
		}
		var env provider.ErrorEnvelope
		if err := json.Unmarshal([]byte(payload), &env); err == nil && env.Error.Message != "" {
			sawError = true
			continue
		}
		var chunk provider.StreamChunk
		if err := json.Unmarshal([]byte(payload), &chunk); err != nil {
			t.Fatalf("emitted a non-JSON frame %q: %v", payload, err)
		}
		if len(chunk.Choices) > 0 {
			text.WriteString(chunk.Choices[0].Delta.Content)
		}
	}
	if text.String() != delivered {
		t.Errorf("delivered text = %q, want %q — frames sent before the failure must survive",
			text.String(), delivered)
	}

	// 3. An error frame followed that text.
	if !sawError {
		t.Error("no in-band error frame; a mid-stream failure must be reported to the client")
	}

	// 4. The stream still terminates, so a client read loop exits normally.
	if !strings.HasSuffix(strings.TrimSpace(body), "data: [DONE]") {
		t.Errorf("stream must still end with data: [DONE]; tail = %q", body[max(0, len(body)-40):])
	}
}
