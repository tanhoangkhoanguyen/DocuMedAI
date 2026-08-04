// Package provider holds LLMGuard's normalized request/response model and the
// per-provider adapters that translate it to and from a vendor's native wire
// format.
//
// The external contract is OpenAI-shaped, so these structs double as the DTOs we
// decode from the client and encode back to it. Core (rate limit, dedup, breaker,
// retry, metrics) only ever sees these types — a vendor's JSON, URL layout and
// auth scheme must not leak past an adapter.
package provider

// --- Request ---

// Message is one turn of the conversation in OpenAI form.
type Message struct {
	Role    string `json:"role"` // system | user | assistant
	Content string `json:"content"`
}

// ChatRequest is an inbound POST /v1/chat/completions body. Unknown fields are
// dropped: a field we don't model can't be forwarded to a provider whose wire
// format is different anyway.
type ChatRequest struct {
	Model       string    `json:"model"`
	Messages    []Message `json:"messages"`
	Temperature *float64  `json:"temperature,omitempty"` // pointer: 0 differs from unset
	MaxTokens   *int      `json:"max_tokens,omitempty"`
	TopP        *float64  `json:"top_p,omitempty"`
	Stop        []string  `json:"stop,omitempty"`
	Stream      bool      `json:"stream,omitempty"`
}

// --- Response ---

// Usage is the token accounting we report to the caller and to Prometheus.
type Usage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
}

// Choice is one completion alternative.
type Choice struct {
	Index        int     `json:"index"`
	Message      Message `json:"message"`
	FinishReason string  `json:"finish_reason"`
}

// ChatResponse is a non-streaming completion in OpenAI form.
type ChatResponse struct {
	ID      string   `json:"id"`
	Object  string   `json:"object"` // "chat.completion"
	Created int64    `json:"created"`
	Model   string   `json:"model"`
	Choices []Choice `json:"choices"`
	Usage   Usage    `json:"usage"`
}

// --- Streaming ---

// Delta is the incremental payload of a stream chunk.
type Delta struct {
	Role    string `json:"role,omitempty"`
	Content string `json:"content,omitempty"`
}

// ChunkChoice is one choice inside a stream chunk.
type ChunkChoice struct {
	Index        int    `json:"index"`
	Delta        Delta  `json:"delta"`
	FinishReason string `json:"finish_reason,omitempty"`
}

// StreamChunk is one `data:` frame of an OpenAI SSE stream. The terminating
// `data: [DONE]` sentinel is written by the caller, not modeled here.
type StreamChunk struct {
	ID      string        `json:"id"`
	Object  string        `json:"object"` // "chat.completion.chunk"
	Created int64         `json:"created"`
	Model   string        `json:"model"`
	Choices []ChunkChoice `json:"choices"`
	Usage   *Usage        `json:"usage,omitempty"` // only on the final chunk, when known
}

// --- Errors ---

// APIError is the OpenAI-shaped error envelope. Adapters translate a vendor's
// error body into this so clients see one error format regardless of provider.
type APIError struct {
	Message string `json:"message"`
	Type    string `json:"type"`
	Code    string `json:"code,omitempty"`
}

// ErrorEnvelope wraps APIError as `{"error": {...}}`.
type ErrorEnvelope struct {
	Error APIError `json:"error"`
}

// NewErrorEnvelope builds the standard error body.
func NewErrorEnvelope(msg, typ string) ErrorEnvelope {
	return ErrorEnvelope{Error: APIError{Message: msg, Type: typ}}
}
