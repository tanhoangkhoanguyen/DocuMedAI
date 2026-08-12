package provider

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"golang.org/x/oauth2"
	"golang.org/x/oauth2/google"
)

// VertexScope is the OAuth2 scope every Vertex AI call needs.
const VertexScope = "https://www.googleapis.com/auth/cloud-platform"

// defaultVertexName is the registry key used when config.yaml names no provider.
const defaultVertexName = "vertex"

// Vertex adapts Google Vertex AI's generateContent API.
//
// Three things differ from an OpenAI-compatible upstream, and all three are why
// a URL-prefix proxy cannot serve Vertex:
//   - the region is in the HOSTNAME and the model is in the PATH, so there is no
//     single "upstream base" to concatenate against;
//   - auth is a short-lived OAuth2 access token from ADC, not a static key;
//   - the wire format is contents/parts, not messages/choices.
type Vertex struct {
	name     string
	project  string
	location string
	tokens   oauth2.TokenSource

	// now supplies the `created` timestamp. It is a field so golden tests can
	// pin it: generateContent returns no timestamp, so the only alternative to a
	// clock is baking a wall-clock value into every fixture.
	now func() time.Time

	// newID supplies the random half of every id this adapter mints — the
	// response id and each tool-call id. A field for the same reason as now: a
	// fixture cannot be compared against a fresh random value.
	newID func() string
}

// timestamp is the `created` value for a translated response.
//
// It tolerates a nil clock because a zero-value &Vertex{} is a legitimate way to
// construct the adapter for translation-only use — internal/gateway's test
// harness does exactly that — and a nil-func panic there would be a landmine
// under code that never touches the network.
func (v *Vertex) timestamp() int64 {
	if v.now == nil {
		return time.Now().Unix()
	}
	return v.now().Unix()
}

// id returns one random identifier, nil-tolerant for the same reason timestamp is.
func (v *Vertex) id() string {
	if v.newID == nil {
		return randomID()
	}
	return v.newID()
}

// randomID returns 16 hex characters of cryptographic randomness.
//
// Ids must be unique, not merely unpredictable, so this reads crypto/rand rather
// than math/rand: the latter's global source is seeded per process, and two
// replicas started from the same image would mint the same sequence.
//
// A read failure is fatal. Falling back to a counter or a timestamp would keep
// the process alive while quietly emitting ids that can collide, and a duplicate
// tool-call id silently attaches a result to the wrong call.
func randomID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic("provider: crypto/rand unavailable: " + err.Error())
	}
	return hex.EncodeToString(b[:])
}

// NewVertex builds the adapter and resolves Application Default Credentials.
// It fails fast: a process that cannot mint a token should not accept traffic.
//
// name is the registry key from config.yaml, as in NewOpenAICompat: the registry
// keys on Name(), so a constant would make every entry named anything else
// unroutable and panic Register on a second instance. Empty defaults to "vertex".
//
// ADC resolution order is the standard one — GOOGLE_APPLICATION_CREDENTIALS, then
// gcloud user credentials, then the attached service account / workload identity.
func NewVertex(ctx context.Context, name, project, location string) (*Vertex, error) {
	if project == "" {
		return nil, fmt.Errorf("vertex: GOOGLE_CLOUD_PROJECT is required")
	}
	if name == "" {
		name = defaultVertexName
	}
	if location == "" {
		location = "us-central1"
	}
	ts, err := google.DefaultTokenSource(ctx, VertexScope)
	if err != nil {
		return nil, fmt.Errorf("vertex: resolve ADC: %w", err)
	}
	return &Vertex{
		name:     name,
		project:  project,
		location: location,
		// ReuseTokenSource caches the token and refreshes it only once it is
		// near expiry, so we don't mint one per request.
		tokens: oauth2.ReuseTokenSource(nil, ts),
	}, nil
}

// newVertexWithTokens is the test seam: same adapter, injected token source.
func newVertexWithTokens(project, location string, ts oauth2.TokenSource) *Vertex {
	if location == "" {
		location = "us-central1"
	}
	return &Vertex{name: defaultVertexName, project: project, location: location, tokens: ts}
}

func (v *Vertex) Name() string { return v.name }

// endpoint builds the fully-qualified Vertex URL for a model.
func (v *Vertex) endpoint(model string, stream bool) string {
	method := "generateContent"
	suffix := ""
	if stream {
		method = "streamGenerateContent"
		suffix = "?alt=sse" // ask for SSE framing rather than a JSON array
	}
	return fmt.Sprintf(
		"https://%s-aiplatform.googleapis.com/v1/projects/%s/locations/%s/publishers/google/models/%s:%s%s",
		v.location, v.project, v.location, model, method, suffix,
	)
}

// --- native wire types ---

// vertexFunctionCall is the model asking to invoke a function. Args is a JSON
// OBJECT here, where OpenAI uses a JSON string — kept raw so the upstream's key
// order survives into the translated `arguments` string. Decoding to a map would
// let encoding/json re-sort the keys.
type vertexFunctionCall struct {
	Name string          `json:"name"`
	Args json.RawMessage `json:"args,omitempty"`
}

// vertexFunctionResponse carries a function's result back to the model. Response
// is an object, so a non-JSON tool result has to be wrapped before it fits.
type vertexFunctionResponse struct {
	Name     string          `json:"name"`
	Response json.RawMessage `json:"response,omitempty"`
}

// vertexPart is a oneof: exactly one field may be set. Text therefore carries
// `omitempty` — emitting `"text":""` alongside a functionCall would make the part
// ambiguous.
type vertexPart struct {
	Text             string                  `json:"text,omitempty"`
	FunctionCall     *vertexFunctionCall     `json:"functionCall,omitempty"`
	FunctionResponse *vertexFunctionResponse `json:"functionResponse,omitempty"`
}

type vertexContent struct {
	Role  string       `json:"role,omitempty"` // user | model ("system" is not a role)
	Parts []vertexPart `json:"parts"`
}

// vertexFunctionDeclaration is one callable function advertised to the model.
// Parameters is the caller's JSON Schema, passed through untouched.
type vertexFunctionDeclaration struct {
	Name        string          `json:"name"`
	Description string          `json:"description,omitempty"`
	Parameters  json.RawMessage `json:"parameters,omitempty"`
}

// vertexTool groups function declarations. Gemini nests them one level deeper
// than OpenAI: a single tool entry holding every declaration, rather than one
// entry per function.
type vertexTool struct {
	FunctionDeclarations []vertexFunctionDeclaration `json:"functionDeclarations,omitempty"`
}

// vertexFunctionCallingConfig is Gemini's equivalent of OpenAI's tool_choice.
type vertexFunctionCallingConfig struct {
	Mode                 string   `json:"mode,omitempty"` // AUTO | ANY | NONE
	AllowedFunctionNames []string `json:"allowedFunctionNames,omitempty"`
}

type vertexToolConfig struct {
	FunctionCallingConfig *vertexFunctionCallingConfig `json:"functionCallingConfig,omitempty"`
}

type vertexGenerationConfig struct {
	Temperature     *float64 `json:"temperature,omitempty"`
	MaxOutputTokens *int     `json:"maxOutputTokens,omitempty"`
	TopP            *float64 `json:"topP,omitempty"`
	StopSequences   []string `json:"stopSequences,omitempty"`
}

type vertexRequest struct {
	Contents          []vertexContent         `json:"contents"`
	SystemInstruction *vertexContent          `json:"systemInstruction,omitempty"`
	GenerationConfig  *vertexGenerationConfig `json:"generationConfig,omitempty"`
	Tools             []vertexTool            `json:"tools,omitempty"`
	ToolConfig        *vertexToolConfig       `json:"toolConfig,omitempty"`
}

type vertexCandidate struct {
	Content      vertexContent `json:"content"`
	FinishReason string        `json:"finishReason"`
	Index        int           `json:"index"`
}

type vertexUsageMetadata struct {
	PromptTokenCount     int `json:"promptTokenCount"`
	CandidatesTokenCount int `json:"candidatesTokenCount"`
	ThoughtsTokenCount   int `json:"thoughtsTokenCount"`
	TotalTokenCount      int `json:"totalTokenCount"`
}

type vertexResponse struct {
	Candidates    []vertexCandidate    `json:"candidates"`
	UsageMetadata *vertexUsageMetadata `json:"usageMetadata"`
	ModelVersion  string               `json:"modelVersion"`
}

type vertexErrorBody struct {
	Error struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
		Status  string `json:"status"`
	} `json:"error"`
}

// --- translation: request ---

// toolCallID synthesizes the id OpenAI requires and Gemini does not provide.
//
// Random rather than derived from the call's content: the model may legitimately
// issue two identical calls in one turn (the same search against two vendors),
// and toNative keys results by id — so ids derived from content would collide and
// one result would be attached to the wrong call. Random also keeps ids opaque
// and uniform with the response id.
func (v *Vertex) toolCallID() string {
	return "call-" + v.id()
}

// argsObject converts OpenAI's `arguments` JSON string into the object Gemini
// expects, preserving the caller's bytes verbatim.
//
// Absent arguments become `{}` — that is a call with no parameters, which is
// legitimate. Arguments that do not PARSE are different: they are corruption,
// and turning them into `{}` would invoke the function with no parameters at
// all, silently, with no way to tell that apart from a genuine no-arg call.
// They are carried through under `_raw` instead, so Gemini's own rejection names
// the actual bytes. This mirrors toolResultPayload below, which likewise wraps
// what it cannot map rather than discarding it.
//
// toNative has no error channel, which is why this reports through the payload
// rather than returning an error.
func argsObject(arguments string) json.RawMessage {
	trimmed := strings.TrimSpace(arguments)
	if trimmed == "" {
		return json.RawMessage("{}")
	}
	if !json.Valid([]byte(trimmed)) {
		// Marshalling a string cannot fail, so the error is unreachable; the
		// fallback keeps the function total rather than panicking on it.
		wrapped, err := json.Marshal(map[string]string{"_raw": arguments})
		if err != nil {
			return json.RawMessage("{}")
		}
		return wrapped
	}
	return json.RawMessage(trimmed)
}

// toolResultPayload converts a role:"tool" message's content into the object
// Gemini's functionResponse requires.
//
// A tool that already returned a JSON object passes through untouched. Anything
// else — a bare string, a number, a JSON array — is wrapped under "result",
// because functionResponse.response must be an object and dropping the value
// would silently discard the tool's answer.
func toolResultPayload(content string) json.RawMessage {
	trimmed := strings.TrimSpace(content)
	if trimmed != "" && json.Valid([]byte(trimmed)) && strings.HasPrefix(trimmed, "{") {
		return json.RawMessage(trimmed)
	}
	wrapped, err := json.Marshal(map[string]string{"result": content})
	if err != nil {
		return json.RawMessage("{}")
	}
	return json.RawMessage(wrapped)
}

// messageParts renders one non-system message as Gemini parts: its text, then a
// functionCall part per tool call.
//
// The empty text part is kept when the message has no tool calls, preserving the
// pre-tool behavior exactly; it is dropped only when a functionCall would
// otherwise share the part with it, which the oneof forbids.
func messageParts(m Message) []vertexPart {
	var parts []vertexPart
	if m.Content != "" || len(m.ToolCalls) == 0 {
		parts = append(parts, vertexPart{Text: m.Content})
	}
	for _, tc := range m.ToolCalls {
		parts = append(parts, vertexPart{FunctionCall: &vertexFunctionCall{
			Name: tc.Function.Name,
			Args: argsObject(tc.Function.Arguments),
		}})
	}
	return parts
}

// toolNamesByCallID indexes every tool call the conversation already declared,
// so a later role:"tool" message can be matched back to its function name.
//
// Gemini's functionResponse is keyed by NAME, while OpenAI's tool result is keyed
// by call ID, and the id is opaque — a client replaying a real OpenAI transcript
// sends ids we never minted, and ours are random. Reading the assistant turns is
// therefore the only mapping available.
func toolNamesByCallID(messages []Message) map[string]string {
	names := make(map[string]string)
	for _, m := range messages {
		for _, tc := range m.ToolCalls {
			if tc.ID != "" {
				names[tc.ID] = tc.Function.Name
			}
		}
	}
	return names
}

// toolChoiceConfig maps OpenAI's tool_choice onto Gemini's
// functionCallingConfig. Returns nil when unset or unrecognized, leaving Gemini
// on its default.
//
// The union is decoded here rather than in the schema: "none" | "auto" |
// "required" as a bare string, or {"type":"function","function":{"name":…}}
// pinning one function.
func toolChoiceConfig(raw json.RawMessage) *vertexToolConfig {
	if len(raw) == 0 {
		return nil
	}

	var mode string
	if err := json.Unmarshal(raw, &mode); err == nil {
		switch mode {
		case "none":
			return &vertexToolConfig{FunctionCallingConfig: &vertexFunctionCallingConfig{Mode: "NONE"}}
		case "auto":
			return &vertexToolConfig{FunctionCallingConfig: &vertexFunctionCallingConfig{Mode: "AUTO"}}
		case "required":
			return &vertexToolConfig{FunctionCallingConfig: &vertexFunctionCallingConfig{Mode: "ANY"}}
		default:
			return nil
		}
	}

	var pinned struct {
		Function struct {
			Name string `json:"name"`
		} `json:"function"`
	}
	if err := json.Unmarshal(raw, &pinned); err != nil || pinned.Function.Name == "" {
		return nil
	}
	// ANY forces a call; allowedFunctionNames narrows it to the one named.
	return &vertexToolConfig{FunctionCallingConfig: &vertexFunctionCallingConfig{
		Mode:                 "ANY",
		AllowedFunctionNames: []string{pinned.Function.Name},
	}}
}

// toNativeTools flattens OpenAI's one-entry-per-function array into Gemini's
// single tool holding every declaration.
func toNativeTools(tools []Tool) []vertexTool {
	if len(tools) == 0 {
		return nil
	}
	decls := make([]vertexFunctionDeclaration, 0, len(tools))
	for _, t := range tools {
		if t.Function.Name == "" {
			continue // unnamed function is uncallable; Gemini rejects the request
		}
		decls = append(decls, vertexFunctionDeclaration{
			Name:        t.Function.Name,
			Description: t.Function.Description,
			Parameters:  t.Function.Parameters,
		})
	}
	if len(decls) == 0 {
		return nil
	}
	return []vertexTool{{FunctionDeclarations: decls}}
}

// toNative converts a normalized request to Vertex's body.
//
// Role mapping: OpenAI "assistant" is Vertex "model"; "user" passes through.
// "system" is NOT a role in Vertex — those messages are hoisted into
// systemInstruction. Consecutive system messages are joined with a blank line.
// "tool" is not a role either: a tool result becomes a functionResponse part on a
// "user" content, which is how Google's own SDKs return results to the model.
func toNative(req *ChatRequest) *vertexRequest {
	out := &vertexRequest{}
	names := toolNamesByCallID(req.Messages)

	var systemParts []string
	for _, m := range req.Messages {
		switch m.Role {
		case "system":
			if m.Content != "" {
				systemParts = append(systemParts, m.Content)
			}

		case "tool":
			// Resolved from the assistant turn that made the call, never parsed
			// out of the id: ids are opaque random strings, and a transcript may
			// carry ids minted by OpenAI rather than by this adapter. A result
			// whose call is absent from the transcript has no name to recover.
			name := names[m.ToolCallID]
			out.Contents = append(out.Contents, vertexContent{
				Role: "user",
				Parts: []vertexPart{{FunctionResponse: &vertexFunctionResponse{
					Name:     name,
					Response: toolResultPayload(m.Content),
				}}},
			})

		default:
			role := "user"
			if m.Role == "assistant" {
				role = "model"
			}
			parts := messageParts(m)
			if len(parts) == 0 {
				continue // nothing to say; an empty parts array is invalid
			}
			out.Contents = append(out.Contents, vertexContent{Role: role, Parts: parts})
		}
	}

	if len(systemParts) > 0 {
		out.SystemInstruction = &vertexContent{
			Parts: []vertexPart{{Text: strings.Join(systemParts, "\n\n")}},
		}
	}

	if req.Temperature != nil || req.MaxTokens != nil || req.TopP != nil || len(req.Stop) > 0 {
		out.GenerationConfig = &vertexGenerationConfig{
			Temperature:     req.Temperature,
			MaxOutputTokens: req.MaxTokens,
			TopP:            req.TopP,
			StopSequences:   req.Stop,
		}
	}

	out.Tools = toNativeTools(req.Tools)
	out.ToolConfig = toolChoiceConfig(req.ToolChoice)
	return out
}

// BuildRequest implements Provider.
func (v *Vertex) BuildRequest(ctx context.Context, req *ChatRequest) (*http.Request, error) {
	body, err := json.Marshal(toNative(req))
	if err != nil {
		return nil, fmt.Errorf("vertex: encode request: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(
		ctx, http.MethodPost, v.endpoint(req.Model, req.Stream), bytes.NewReader(body))
	if err != nil {
		return nil, err
	}

	// Minted per attempt so a retry after a long backoff never sends a token
	// that expired while we waited.
	tok, err := v.tokens.Token()
	if err != nil {
		return nil, fmt.Errorf("vertex: get access token: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")
	tok.SetAuthHeader(httpReq)
	return httpReq, nil
}

// --- translation: response ---

// finishReason maps Vertex finish reasons onto OpenAI's smaller vocabulary.
func finishReason(v string) string {
	switch v {
	case "STOP":
		return "stop"
	case "MAX_TOKENS":
		return "length"
	case "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII":
		return "content_filter"
	case "":
		return ""
	default:
		return "stop"
	}
}

// joinParts concatenates a candidate's text parts. Vertex may split one logical
// message across several parts; OpenAI has a single content string.
//
// functionCall parts contribute nothing here — their Text is empty and they are
// translated separately by toolCalls.
func joinParts(parts []vertexPart) string {
	if len(parts) == 1 {
		return parts[0].Text
	}
	var b strings.Builder
	for _, p := range parts {
		b.WriteString(p.Text)
	}
	return b.String()
}

// toolCalls translates a candidate's functionCall parts into OpenAI tool calls.
//
// Arguments crosses a type boundary: Gemini sends an object, OpenAI expects a
// JSON string, so the raw bytes are carried across as-is rather than re-encoded.
// An absent args becomes "{}" — OpenAI clients parse this field, and "" is not
// valid JSON.
func (v *Vertex) toolCalls(parts []vertexPart) []ToolCall {
	var calls []ToolCall
	for _, p := range parts {
		if p.FunctionCall == nil {
			continue
		}
		args := "{}"
		if len(p.FunctionCall.Args) > 0 {
			args = string(p.FunctionCall.Args)
		}
		calls = append(calls, ToolCall{
			ID:   v.toolCallID(),
			Type: "function",
			Function: FunctionCall{
				Name:      p.FunctionCall.Name,
				Arguments: args,
			},
		})
	}
	return calls
}

// finishReasonFor applies OpenAI's rule that a turn ending in tool calls reports
// "tool_calls", which Gemini does not do — it reports STOP and lets the presence
// of functionCall parts speak for itself.
//
// A non-stop reason wins: MAX_TOKENS while emitting a partial call is still a
// truncation, and reporting "tool_calls" there would hide it.
func finishReasonFor(native string, calls int) string {
	mapped := finishReason(native)
	if calls > 0 && (mapped == "stop" || mapped == "") {
		return "tool_calls"
	}
	return mapped
}

// toolCallDeltas renders complete tool calls as stream fragments.
//
// OpenAI splits a call across frames and expects the client to concatenate the
// `arguments` fragments. Gemini does not fragment: a functionCall arrives whole
// in one frame. So each call is emitted as a single, already-complete delta —
// valid under OpenAI's contract, since one fragment is a legal fragmentation.
//
// QUIRK: Index is the call's position WITHIN THIS FRAME. TranslateStreamChunk is
// stateless per frame, so if Gemini ever splits functionCalls of one turn across
// frames, indices would restart at 0 and a client would merge distinct calls.
// Not observed today — Gemini emits them together — and fixing it properly means
// giving the interface per-turn state, which belongs with the proxy.
func toolCallDeltas(calls []ToolCall) []ToolCallDelta {
	if len(calls) == 0 {
		return nil
	}
	deltas := make([]ToolCallDelta, 0, len(calls))
	for i, c := range calls {
		deltas = append(deltas, ToolCallDelta{
			Index: i,
			ID:    c.ID,
			Type:  c.Type,
			Function: &FunctionCallDelta{
				Name:      c.Function.Name,
				Arguments: c.Function.Arguments,
			},
		})
	}
	return deltas
}

// responseID synthesizes the id OpenAI requires; generateContent returns none.
//
// Random rather than a hash of the response bytes: clients use this id to trace
// and de-duplicate, so two callers who happen to receive identical content must
// still get distinct ids. The "chatcmpl-" prefix is part of OpenAI's contract.
func (v *Vertex) responseID() string {
	return "chatcmpl-" + v.id()
}

// toUsage converts Vertex token accounting.
//
// thoughtsTokenCount (thinking models) is folded into completion tokens: it is
// billed as output and OpenAI has no field for it, so hiding it would understate
// cost.
func toUsage(m *vertexUsageMetadata) Usage {
	if m == nil {
		return Usage{}
	}
	completion := m.CandidatesTokenCount + m.ThoughtsTokenCount
	total := m.TotalTokenCount
	if total == 0 {
		total = m.PromptTokenCount + completion
	}
	return Usage{
		PromptTokens:     m.PromptTokenCount,
		CompletionTokens: completion,
		TotalTokens:      total,
	}
}

// TranslateResponse implements Provider.
func (v *Vertex) TranslateResponse(status int, body []byte) (*ChatResponse, error) {
	if status < 200 || status > 299 {
		return nil, &UpstreamError{Status: status, Body: vertexErrorEnvelope(status, body)}
	}

	var native vertexResponse
	if err := json.Unmarshal(body, &native); err != nil {
		return nil, fmt.Errorf("vertex: decode response: %w", err)
	}

	out := &ChatResponse{
		ID:      v.responseID(),
		Object:  "chat.completion",
		Created: v.timestamp(),
		Model:   native.ModelVersion,
		Choices: make([]Choice, 0, len(native.Candidates)),
		Usage:   toUsage(native.UsageMetadata),
	}
	for i, c := range native.Candidates {
		idx := c.Index
		if idx == 0 {
			idx = i
		}
		calls := v.toolCalls(c.Content.Parts)
		out.Choices = append(out.Choices, Choice{
			Index: idx,
			Message: Message{
				Role:      "assistant",
				Content:   joinParts(c.Content.Parts),
				ToolCalls: calls,
			},
			FinishReason: finishReasonFor(c.FinishReason, len(calls)),
		})
	}
	return out, nil
}

// vertexErrorEnvelope turns a Vertex error body into the OpenAI error shape,
// falling back to the raw body when it isn't the expected JSON.
func vertexErrorEnvelope(status int, body []byte) ErrorEnvelope {
	var ve vertexErrorBody
	if err := json.Unmarshal(body, &ve); err == nil && ve.Error.Message != "" {
		return NewErrorEnvelope(ve.Error.Message, errorType(status))
	}
	msg := strings.TrimSpace(string(body))
	if msg == "" {
		msg = fmt.Sprintf("upstream returned status %d", status)
	}
	return NewErrorEnvelope(msg, errorType(status))
}

func errorType(status int) string {
	switch {
	case status == http.StatusTooManyRequests:
		return "rate_limit"
	case status == http.StatusUnauthorized || status == http.StatusForbidden:
		return "auth_error"
	case status >= 500:
		return "upstream_error"
	default:
		return "invalid_request_error"
	}
}

// --- translation: streaming ---

// TranslateStreamChunk implements Provider. `raw` is the payload of one SSE
// `data:` line, already stripped of the prefix by the caller.
func (v *Vertex) TranslateStreamChunk(req *ChatRequest, raw []byte) ([]StreamChunk, error) {
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 {
		return nil, nil
	}

	var native vertexResponse
	if err := json.Unmarshal(raw, &native); err != nil {
		return nil, fmt.Errorf("vertex: decode stream chunk: %w", err)
	}

	model := native.ModelVersion
	if model == "" {
		model = req.Model
	}

	chunk := StreamChunk{
		Object:  "chat.completion.chunk",
		Model:   model,
		Choices: make([]ChunkChoice, 0, len(native.Candidates)),
	}
	for i, c := range native.Candidates {
		idx := c.Index
		if idx == 0 {
			idx = i
		}
		calls := v.toolCalls(c.Content.Parts)
		chunk.Choices = append(chunk.Choices, ChunkChoice{
			Index: idx,
			Delta: Delta{
				Content:   joinParts(c.Content.Parts),
				ToolCalls: toolCallDeltas(calls),
			},
			FinishReason: finishReasonFor(c.FinishReason, len(calls)),
		})
	}

	// Vertex repeats usageMetadata on every frame; only the final one is
	// complete. Attach it when the turn is finishing so the caller can account
	// tokens on the streaming path too.
	if native.UsageMetadata != nil && hasFinish(native.Candidates) {
		u := toUsage(native.UsageMetadata)
		chunk.Usage = &u
	}

	// A frame with no candidates (metadata-only) yields no client-visible chunk.
	if len(chunk.Choices) == 0 && chunk.Usage == nil {
		return nil, nil
	}
	return []StreamChunk{chunk}, nil
}

func hasFinish(cands []vertexCandidate) bool {
	for _, c := range cands {
		if c.FinishReason != "" {
			return true
		}
	}
	return false
}
