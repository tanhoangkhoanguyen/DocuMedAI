// Package provider holds LLMGuard's normalized request/response model and the
// per-provider adapters that translate it to and from a vendor's native wire
// format.
//
// The external contract is OpenAI-shaped, so these structs double as the DTOs we
// decode from the client and encode back to it. Core (rate limit, dedup, breaker,
// retry, metrics) only ever sees these types — a vendor's JSON, URL layout and
// auth scheme must not leak past an adapter.
package provider

import "encoding/json"

// --- Request ---

// Message is one turn of the conversation in OpenAI form.
type Message struct {
	Role    string `json:"role"` // system | user | assistant | tool
	Content string `json:"content"`

	// ToolCalls is set on an assistant turn where the model asked to invoke one
	// or more functions instead of (or alongside) replying with text.
	ToolCalls []ToolCall `json:"tool_calls,omitempty"`

	// ToolCallID is set on a role:"tool" turn and names which ToolCall this
	// message carries the result of. Content holds the result payload.
	ToolCallID string `json:"tool_call_id,omitempty"`
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

	// Tools are the functions the model may call.
	Tools []Tool `json:"tools,omitempty"`

	// ToolChoice constrains which tool the model may pick. It stays raw because
	// OpenAI models it as a UNION: the string "none", "auto" or "required", or an
	// object naming one function. A typed string field would silently fail to
	// decode the object form, and a typed struct would fail on the string form.
	ToolChoice json.RawMessage `json:"tool_choice,omitempty"`
}

// --- Tools ---

// FunctionDef describes one function the model may call.
type FunctionDef struct {
	// Name is the identifier the model uses to invoke the function.
	Name string `json:"name"`

	// Description tells the model when to call it.
	Description string `json:"description,omitempty"`

	// Parameters is a JSON Schema object describing the arguments. It stays raw
	// because it is arbitrary user-supplied schema: modelling it would constrain
	// what callers can express and force a lossy round trip, while adapters only
	// ever pass it through.
	Parameters json.RawMessage `json:"parameters,omitempty"`
}

// Tool is one entry of a request's `tools` array.
type Tool struct {
	// Type is the tool kind. OpenAI defines only "function" today.
	Type string `json:"type"`

	// Function is the callable definition.
	Function FunctionDef `json:"function"`
}

// FunctionCall is the invocation half of a tool call.
type FunctionCall struct {
	// Name is the function the model chose.
	Name string `json:"name"`

	// Arguments is a JSON *string* containing the argument object — OpenAI's
	// encoding, not a nested object. Adapters translating from a provider that
	// sends a real object must marshal it into this string.
	Arguments string `json:"arguments"`
}

// ToolCall is one function invocation requested by the model.
type ToolCall struct {
	// ID correlates this call with the role:"tool" message carrying its result.
	ID string `json:"id"`

	// Type is the tool kind, always "function" today.
	Type string `json:"type"`

	// Function names the call and carries its arguments.
	Function FunctionCall `json:"function"`
}

// --- Streaming ---

// FunctionCallDelta is the incremental half of a streamed tool call. OpenAI
// streams `arguments` as string fragments that a client concatenates, so a delta
// may carry a partial, individually invalid JSON string.
type FunctionCallDelta struct {
	// Name appears on the fragment that opens a call, then is omitted.
	Name string `json:"name,omitempty"`

	// Arguments is a fragment of the JSON argument string, not necessarily valid
	// JSON on its own.
	Arguments string `json:"arguments,omitempty"`
}

// ToolCallDelta is one streamed fragment of a tool call.
type ToolCallDelta struct {
	// Index identifies which tool call in the message this fragment belongs to.
	// It is how a client reassembles interleaved calls, so it is always emitted —
	// index 0 is meaningful and must not be omitted.
	Index int `json:"index"`

	// ID appears on the fragment that opens a call, then is omitted.
	ID string `json:"id,omitempty"`

	// Type appears on the fragment that opens a call, then is omitted.
	Type string `json:"type,omitempty"`

	// Function carries the name and argument fragments. A pointer so a fragment
	// with no function payload omits the key entirely — `omitempty` has no effect
	// on a struct value.
	Function *FunctionCallDelta `json:"function,omitempty"`
}

// Delta is the incremental payload of a stream chunk.
type Delta struct {
	Role    string `json:"role,omitempty"`
	Content string `json:"content,omitempty"`

	// ToolCalls carries streamed tool-call fragments.
	ToolCalls []ToolCallDelta `json:"tool_calls,omitempty"`
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
	Index   int     `json:"index"`
	Message Message `json:"message"`

	// FinishReason is OpenAI's vocabulary: stop | length | content_filter |
	// tool_calls. "tool_calls" means the model stopped in order to invoke the
	// functions listed in Message.ToolCalls.
	FinishReason string `json:"finish_reason"`
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
