// Package vertex adapts Google Vertex AI's native generateContent API.
//
// This is a NATIVE adapter, the exception rather than the default. Three things
// put Vertex outside what provider/openai can serve by configuration alone:
//   - the region is in the HOSTNAME and the model is in the PATH, so there is no
//     single "upstream base" to concatenate against;
//   - auth is a short-lived OAuth2 access token from ADC, not a static key;
//   - the wire format is contents/parts, not messages/choices.
//
// Only a vendor that breaks the OpenAI format this way needs a package here.
// Anything that speaks /chat/completions — including Gemini's own compat
// endpoint — belongs in config.yaml as a `type: openai-compat` entry instead.
//
// Credentials come from Application Default Credentials, so the same binary
// authenticates from a JSON key file locally (GOOGLE_APPLICATION_CREDENTIALS)
// and from the attached service account on GCP, with no code change.
package vertex

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

	"documedai/llmguard/provider"
)

// Scope is the OAuth2 scope every Vertex AI call needs.
const Scope = "https://www.googleapis.com/auth/cloud-platform"

// defaultName is the registry key used when config.yaml names no provider.
const defaultName = "vertex"

// Client adapts one Vertex AI project+region. See the package doc for why
// Vertex needs a native adapter at all.
type Client struct {
	name     string
	project  string
	location string
	tokens   oauth2.TokenSource

	// now supplies the `created` timestamp. It is a field so golden tests can
	// pin it: generateContent returns no timestamp, so the only alternative to a
	// clock is baking a wall-clock value into every fixture.
	now func() time.Time

	// newID supplies the random half of the response id this adapter mints. A
	// field for the same reason as now: a fixture cannot be compared against a
	// fresh random value.
	newID func() string
}

// timestamp is the `created` value for a translated response.
//
// It tolerates a nil clock because a zero-value &Client{} is a legitimate way to
// construct the adapter for translation-only use — internal/gateway's test
// harness does exactly that — and a nil-func panic there would be a landmine
// under code that never touches the network.
func (v *Client) timestamp() int64 {
	if v.now == nil {
		return time.Now().Unix()
	}
	return v.now().Unix()
}

// id returns one random identifier, nil-tolerant for the same reason timestamp is.
func (v *Client) id() string {
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
// the process alive while quietly emitting ids that can collide, and clients use
// the response id to trace and de-duplicate.
func randomID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic("provider: crypto/rand unavailable: " + err.Error())
	}
	return hex.EncodeToString(b[:])
}

// New builds the adapter and resolves Application Default Credentials.
// It fails fast: a process that cannot mint a token should not accept traffic.
//
// name is the registry key from config.yaml, as in openai.New: the registry
// keys on Name(), so a constant would make every entry named anything else
// unroutable and panic Register on a second instance. Empty defaults to "vertex".
//
// ADC resolution order is the standard one — GOOGLE_APPLICATION_CREDENTIALS, then
// gcloud user credentials, then the attached service account / workload identity.
func New(ctx context.Context, name, project, location string) (*Client, error) {
	if project == "" {
		return nil, fmt.Errorf("vertex: GOOGLE_CLOUD_PROJECT is required")
	}
	if name == "" {
		name = defaultName
	}
	if location == "" {
		location = "us-central1"
	}
	ts, err := google.DefaultTokenSource(ctx, Scope)
	if err != nil {
		return nil, fmt.Errorf("vertex: resolve ADC: %w", err)
	}
	return &Client{
		name:     name,
		project:  project,
		location: location,
		// ReuseTokenSource caches the token and refreshes it only once it is
		// near expiry, so we don't mint one per request.
		tokens: oauth2.ReuseTokenSource(nil, ts),
	}, nil
}

// newWithTokens is the test seam: same adapter, injected token source.
func newWithTokens(project, location string, ts oauth2.TokenSource) *Client {
	if location == "" {
		location = "us-central1"
	}
	return &Client{name: defaultName, project: project, location: location, tokens: ts}
}

func (v *Client) Name() string { return v.name }

// endpoint builds the fully-qualified Vertex URL for a model.
func (v *Client) endpoint(model string, stream bool) string {
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

// vertexPart carries one piece of a content. Text keeps `omitempty` because the
// emitted shape is pinned by the golden fixtures: a message with empty content
// still emits a part, and it emits it as `{}`.
type vertexPart struct {
	Text string `json:"text,omitempty"`
}

type vertexContent struct {
	Role  string       `json:"role,omitempty"` // user | model ("system" is not a role)
	Parts []vertexPart `json:"parts"`
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

// toNative converts a normalized request to Vertex's body.
//
// Role mapping: OpenAI "assistant" is Vertex "model"; "user" passes through.
// "system" is NOT a role in Vertex — those messages are hoisted into
// systemInstruction. Consecutive system messages are joined with a blank line.
func toNative(req *provider.ChatRequest) *vertexRequest {
	out := &vertexRequest{}

	var systemParts []string
	for _, m := range req.Messages {
		switch m.Role {
		case "system":
			if m.Content != "" {
				systemParts = append(systemParts, m.Content)
			}

		default:
			role := "user"
			if m.Role == "assistant" {
				role = "model"
			}
			out.Contents = append(out.Contents, vertexContent{
				Role:  role,
				Parts: []vertexPart{{Text: m.Content}},
			})
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

	return out
}

// BuildRequest implements Provider.
func (v *Client) BuildRequest(ctx context.Context, req *provider.ChatRequest) (*http.Request, error) {
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

// responseID synthesizes the id OpenAI requires; generateContent returns none.
//
// Random rather than a hash of the response bytes: clients use this id to trace
// and de-duplicate, so two callers who happen to receive identical content must
// still get distinct ids. The "chatcmpl-" prefix is part of OpenAI's contract.
func (v *Client) responseID() string {
	return "chatcmpl-" + v.id()
}

// toUsage converts Vertex token accounting.
//
// thoughtsTokenCount (thinking models) is folded into completion tokens: it is
// billed as output and OpenAI has no field for it, so hiding it would understate
// cost.
func toUsage(m *vertexUsageMetadata) provider.Usage {
	if m == nil {
		return provider.Usage{}
	}
	completion := m.CandidatesTokenCount + m.ThoughtsTokenCount
	total := m.TotalTokenCount
	if total == 0 {
		total = m.PromptTokenCount + completion
	}
	return provider.Usage{
		PromptTokens:     m.PromptTokenCount,
		CompletionTokens: completion,
		TotalTokens:      total,
	}
}

// TranslateResponse implements Provider.
func (v *Client) TranslateResponse(status int, body []byte) (*provider.ChatResponse, error) {
	if status < 200 || status > 299 {
		return nil, &provider.UpstreamError{Status: status, Body: vertexErrorEnvelope(status, body)}
	}

	var native vertexResponse
	if err := json.Unmarshal(body, &native); err != nil {
		return nil, fmt.Errorf("vertex: decode response: %w", err)
	}

	out := &provider.ChatResponse{
		ID:      v.responseID(),
		Object:  "chat.completion",
		Created: v.timestamp(),
		Model:   native.ModelVersion,
		Choices: make([]provider.Choice, 0, len(native.Candidates)),
		Usage:   toUsage(native.UsageMetadata),
	}
	for i, c := range native.Candidates {
		idx := c.Index
		if idx == 0 {
			idx = i
		}
		out.Choices = append(out.Choices, provider.Choice{
			Index: idx,
			Message: provider.Message{
				Role:    "assistant",
				Content: joinParts(c.Content.Parts),
			},
			FinishReason: finishReason(c.FinishReason),
		})
	}
	return out, nil
}

// vertexErrorEnvelope turns a Vertex error body into the OpenAI error shape,
// falling back to the raw body when it isn't the expected JSON.
func vertexErrorEnvelope(status int, body []byte) provider.ErrorEnvelope {
	var ve vertexErrorBody
	if err := json.Unmarshal(body, &ve); err == nil && ve.Error.Message != "" {
		return provider.NewErrorEnvelope(ve.Error.Message, provider.ErrorType(status))
	}
	msg := strings.TrimSpace(string(body))
	if msg == "" {
		msg = fmt.Sprintf("upstream returned status %d", status)
	}
	return provider.NewErrorEnvelope(msg, provider.ErrorType(status))
}

// --- translation: streaming ---

// TranslateStreamChunk implements Provider. `raw` is the payload of one SSE
// `data:` line, already stripped of the prefix by the caller.
func (v *Client) TranslateStreamChunk(req *provider.ChatRequest, raw []byte) ([]provider.StreamChunk, error) {
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

	chunk := provider.StreamChunk{
		Object:  "chat.completion.chunk",
		Model:   model,
		Choices: make([]provider.ChunkChoice, 0, len(native.Candidates)),
	}
	for i, c := range native.Candidates {
		idx := c.Index
		if idx == 0 {
			idx = i
		}
		chunk.Choices = append(chunk.Choices, provider.ChunkChoice{
			Index: idx,
			Delta: provider.Delta{
				Content: joinParts(c.Content.Parts),
			},
			FinishReason: finishReason(c.FinishReason),
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
	return []provider.StreamChunk{chunk}, nil
}

func hasFinish(cands []vertexCandidate) bool {
	for _, c := range cands {
		if c.FinishReason != "" {
			return true
		}
	}
	return false
}
