package gateway

// Characterization: usage-token extraction, buffered and streaming.
// Harness and thresholds live in harness_test.go.

import (
	"net/http"
	"testing"

	"documedai/llmguard/internal/testutil"
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

	// The same counts must reach Prometheus, not just the response body.
	if v := testutil.LabeledCounterValue(t, h.metrics.tokensUsed, "gemini-2.5-flash", "completion"); v != completionTokens {
		t.Errorf("tokensUsed{completion} = %v, want %d", v, completionTokens)
	}
	if v := testutil.LabeledCounterValue(t, h.metrics.tokensUsed, "gemini-2.5-flash", "prompt"); v != float64(got.Usage.PromptTokens) {
		t.Errorf("tokensUsed{prompt} = %v, want %d", v, got.Usage.PromptTokens)
	}
}

// Usage is also accounted on the streaming path, from the finishing chunk.
func TestUsageTokenExtractionStreaming(t *testing.T) {
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
