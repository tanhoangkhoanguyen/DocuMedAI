package gateway

// Characterization: usage-token extraction on the buffered path.
// Harness and thresholds live in harness_test.go.

import (
	"net/http"
	"testing"

	"documedai/llmguard/mockupstream"
)

func TestUsageTokenExtraction(t *testing.T) {
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
}
