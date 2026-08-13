package provider

import (
	"encoding/json"
	"testing"
)

// TestGoldenAssistantMessageShape pins the one Message every client sees.
//
// Message carries the tool fields, and each one must be omitempty or this body
// grows keys existing clients do not expect. This is the narrowest guard on
// that: an assistant turn with empty content stays exactly two keys.
//
// It lives with the schema rather than with the Vertex fixtures it used to sit
// beside: what it pins is the normalized wire form every adapter shares, so a
// change here breaks all of them, not just one vendor.
func TestGoldenAssistantMessageShape(t *testing.T) {
	t.Parallel()

	got, err := json.Marshal(Message{Role: "assistant", Content: ""})
	if err != nil {
		t.Fatalf("marshal message: %v", err)
	}
	if want := `{"role":"assistant","content":""}`; string(got) != want {
		t.Errorf("Message wire form = %s, want %s", got, want)
	}
}
