package mockupstream

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// oaRequest is the subset of the OpenAI chat-completion request the mock reads.
type oaRequest struct {
	Model         string      `json:"model"`
	Messages      []oaReqMsg  `json:"messages"`
	Stream        bool        `json:"stream"`
	StreamOptions *oaStreamOp `json:"stream_options"`
	MaxTokens     int         `json:"max_tokens"`
}

type oaStreamOp struct {
	IncludeUsage bool `json:"include_usage"`
}

// oaReqMsg tolerates both content forms: a plain string, and the multimodal
// array of parts. Decoding into json.RawMessage and branching keeps a
// multimodal request from failing to parse and silently reporting zero tokens.
type oaReqMsg struct {
	Role    string          `json:"role"`
	Content json.RawMessage `json:"content"`
}

// text flattens a message's content to a string for token estimation.
func (m oaReqMsg) text() string {
	if len(m.Content) == 0 {
		return ""
	}
	var s string
	if json.Unmarshal(m.Content, &s) == nil {
		return s
	}
	var parts []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if json.Unmarshal(m.Content, &parts) == nil {
		var b strings.Builder
		for _, p := range parts {
			b.WriteString(p.Text)
			b.WriteString(" ")
		}
		return strings.TrimSpace(b.String())
	}
	return ""
}

// handleChatCompletions serves POST /v1/chat/completions in both buffered and
// SSE streaming form, selected by the request's own `stream` field — the same
// switch a real provider makes.
func (s *Server) handleChatCompletions(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeFailure(w, http.StatusMethodNotAllowed, 0, "method_not_allowed", false)
		return
	}

	body, err := io.ReadAll(r.Body)
	if err != nil {
		writeFailure(w, http.StatusBadRequest, 0, "unreadable_body", false)
		return
	}

	cfg := Resolve(s.cfg, r)
	var req oaRequest
	// A malformed body is not fatal: it still exercises the proxy's forwarding
	// path, and the zero value produces a valid (if empty) completion.
	_ = json.Unmarshal(body, &req)

	model := req.Model
	if model == "" {
		model = cfg.Model
	}

	// Outage first: a total outage overrides every other consideration.
	if s.outage.active() {
		writeFailure(w, cfg.ErrorStatus, cfg.RetryAfter, "outage", false)
		return
	}

	verdict := decide(cfg, r, body)
	if !verdict.sleep(r) {
		return // client hung up mid-delay
	}
	if verdict.fail {
		writeFailure(w, verdict.status, cfg.RetryAfter, "error_rate", false)
		return
	}

	var prompt strings.Builder
	for _, m := range req.Messages {
		prompt.WriteString(m.text())
		prompt.WriteString(" ")
	}
	promptTokens := estimateTokens(strings.TrimSpace(prompt.String()))

	words := completionWords(cfg, body)
	// max_tokens truncates the reply, as a real provider would.
	if req.MaxTokens > 0 && len(words) > req.MaxTokens {
		words = words[:req.MaxTokens]
	}

	if req.Stream {
		s.streamChatCompletion(w, r, cfg, model, body, words, promptTokens)
		return
	}
	s.bufferedChatCompletion(w, cfg, model, body, words, promptTokens)
}

func (s *Server) bufferedChatCompletion(
	w http.ResponseWriter, cfg Config, model string, body []byte, words []string, promptTokens int,
) {
	text := strings.Join(words, " ")
	resp := oaCompletion{
		ID:      responseID("chatcmpl", cfg, body),
		Object:  "chat.completion",
		Created: cfg.Created,
		Model:   model,
		Choices: []oaChoice{{
			Index:        0,
			Message:      oaMessage{Role: "assistant", Content: text},
			FinishReason: "stop",
		}},
		Usage: oaUsage{
			PromptTokens:     promptTokens,
			CompletionTokens: len(words),
			TotalTokens:      promptTokens + len(words),
		},
		SystemFingerprint: "fp_mockupstream",
	}

	encoded, err := json.Marshal(resp)
	if err != nil {
		writeFailure(w, http.StatusInternalServerError, 0, "encode_failed", false)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Mock-Upstream", "1")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(encoded)
}

// streamChatCompletion emits the OpenAI SSE sequence: a role-only delta, one
// delta per word, a terminating chunk carrying finish_reason, an optional usage
// chunk, then [DONE].
//
// Every chunk is flushed individually — without that the whole stream would
// arrive as one buffered write and time-to-first-token would be meaningless.
func (s *Server) streamChatCompletion(
	w http.ResponseWriter, r *http.Request, cfg Config, model string, body []byte, words []string, promptTokens int,
) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeFailure(w, http.StatusInternalServerError, 0, "streaming_unsupported", false)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Mock-Upstream", "1")
	w.WriteHeader(http.StatusOK)

	id := responseID("chatcmpl", cfg, body)
	base := oaChunk{ID: id, Object: "chat.completion.chunk", Created: cfg.Created, Model: model}

	send := func(c oaChunk) bool {
		encoded, err := json.Marshal(c)
		if err != nil {
			return false
		}
		if _, werr := fmt.Fprintf(w, "data: %s\n\n", encoded); werr != nil {
			return false
		}
		flusher.Flush()
		return true
	}

	// Opening chunk announces the role and carries no content, matching OpenAI.
	first := base
	first.Choices = []oaChunkChoice{{Index: 0, Delta: oaDelta{Role: "assistant"}}}
	if !send(first) {
		return
	}

	for i, word := range words {
		// Word-boundary spacing lives in the deltas, so a client that simply
		// concatenates them reproduces the buffered text exactly.
		chunkText := word
		if i > 0 {
			chunkText = " " + word
		}
		c := base
		c.Choices = []oaChunkChoice{{Index: 0, Delta: oaDelta{Content: chunkText}}}
		if !send(c) {
			return
		}
		if cfg.ChunkDelay > 0 {
			select {
			case <-time.After(cfg.ChunkDelay):
			case <-r.Context().Done():
				return
			}
		}
	}

	stop := "stop"
	last := base
	last.Choices = []oaChunkChoice{{Index: 0, Delta: oaDelta{}, FinishReason: &stop}}
	if !send(last) {
		return
	}

	// Usage is only emitted when the caller opted in, mirroring the real API.
	if r.URL.Query().Get("include_usage") == "1" || streamWantsUsage(body) {
		usage := base
		usage.Choices = []oaChunkChoice{}
		usage.Usage = &oaUsage{
			PromptTokens:     promptTokens,
			CompletionTokens: len(words),
			TotalTokens:      promptTokens + len(words),
		}
		if !send(usage) {
			return
		}
	}

	_, _ = io.WriteString(w, "data: [DONE]\n\n")
	flusher.Flush()
}

// streamWantsUsage re-reads stream_options.include_usage from the raw body.
func streamWantsUsage(body []byte) bool {
	var req oaRequest
	if json.Unmarshal(body, &req) != nil {
		return false
	}
	return req.StreamOptions != nil && req.StreamOptions.IncludeUsage
}

// handleModels serves GET /v1/models so tooling that probes for capability
// before issuing a completion does not get a 404.
func (s *Server) handleModels(w http.ResponseWriter, _ *http.Request) {
	type model struct {
		ID      string `json:"id"`
		Object  string `json:"object"`
		Created int64  `json:"created"`
		OwnedBy string `json:"owned_by"`
	}
	payload := struct {
		Object string  `json:"object"`
		Data   []model `json:"data"`
	}{
		Object: "list",
		Data: []model{
			{ID: s.cfg.Model, Object: "model", Created: s.cfg.Created, OwnedBy: "mockupstream"},
		},
	}
	writeJSON(w, http.StatusOK, payload)
}
