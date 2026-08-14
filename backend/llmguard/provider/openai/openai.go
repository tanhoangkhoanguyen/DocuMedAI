// Package openai adapts any upstream that already speaks OpenAI's
// /chat/completions wire format.
//
// This is the GENERIC adapter and the default way to add a vendor: OpenAI itself,
// OpenRouter, Groq, Together, DeepSeek, a self-hosted vLLM, and Gemini's
// OpenAI-compatibility endpoint are all reached by configuration alone — a
// `type: openai-compat` entry in config.yaml, no code. A vendor needs its own
// package (as provider/vertex has) only when it does NOT speak this format.
package openai

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"documedai/llmguard/provider"
)

// defaultName is the registry key used when config.yaml names no provider.
const defaultName = "openai-compat"

// Client adapts one OpenAI-compatible upstream.
//
// Because LLMGuard's canonical shape IS OpenAI's shape, translation here is
// nearly the identity: the request marshals straight back out and the response
// unmarshals straight in. What the adapter actually owns is the parts that
// differ per deployment — endpoint URL and credential — which is why one
// instance per upstream, configured with a base URL and key, covers every
// vendor above without a line of per-vendor code.
type Client struct {
	name    string
	baseURL string
	apiKey  string
}

// Compile-time proof the adapter satisfies the interface.
var _ provider.Provider = (*Client)(nil)

// New builds an adapter for one OpenAI-compatible upstream.
//
// baseURL must include the vendor's version prefix and NOT the
// "/chat/completions" suffix — "https://api.openai.com/v1",
// "https://openrouter.ai/api/v1", "https://generativelanguage.googleapis.com/v1beta/openai".
// That is the same convention every OpenAI SDK uses for base_url, and it is the
// only one under which all the supported vendors work by configuration alone:
// their version segments genuinely differ, so a hard-coded "/v1" would send
// Gemini-compat traffic to ".../v1beta/openai/v1/chat/completions".
//
// apiKey may be empty for an upstream that does not authenticate (a local vLLM);
// the Authorization header is then omitted rather than sent empty. name is the
// registry key and defaults to "openai-compat" when blank, so two upstreams can
// be registered side by side under distinct names.
func New(name, baseURL, apiKey string) (*Client, error) {
	if baseURL == "" {
		return nil, fmt.Errorf("openai-compat: baseURL is required")
	}
	if name == "" {
		name = defaultName
	}
	return &Client{
		name: name,
		// Trimmed once at construction so endpoint() stays a plain concatenation
		// and a trailing slash in config cannot produce a doubled "//".
		baseURL: strings.TrimRight(baseURL, "/"),
		apiKey:  apiKey,
	}, nil
}

func (o *Client) Name() string { return o.name }

// endpoint builds the chat-completions URL for this upstream.
func (o *Client) endpoint() string {
	return o.baseURL + "/chat/completions"
}

// --- translation: request ---

// BuildRequest implements provider.Provider.
//
// The canonical request is already the wire format, so it is marshalled as-is;
// `stream` rides along in the body where an OpenAI-compatible upstream expects
// it, rather than in the URL. Ported from the pre-adapter buildUpstreamRequest:
// the caller's key never reaches the upstream — LLMGuard substitutes its own.
func (o *Client) BuildRequest(ctx context.Context, req *provider.ChatRequest) (*http.Request, error) {
	// Provider is LLMGuard's own routing field and means nothing upstream, so it
	// is stripped rather than forwarded: OpenAI itself rejects a body carrying an
	// unrecognized field. Copied by value — mutating the caller's request would
	// leak into the retry loop, which reuses the same struct across attempts.
	outbound := *req
	outbound.Provider = ""

	// Encode JSON
	body, err := json.Marshal(&outbound)
	if err != nil {
		return nil, fmt.Errorf("openai-compat: encode request: %w", err)
	}

	// Create HTTP POST
	httpReq, err := http.NewRequestWithContext(
		ctx, http.MethodPost, o.endpoint(), bytes.NewReader(body))
	if err != nil {
		return nil, err
	}

	// Create Header
	httpReq.Header.Set("Content-Type", "application/json")
	if o.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+o.apiKey)
	}
	return httpReq, nil
}

// --- translation: response ---

// TranslateResponse implements provider.Provider.
//
// A 2xx body is already OpenAI-shaped, so it decodes directly into ChatResponse
// — which carries `id`, `created` and the `usage` block through to the client.
// The Vertex adapter cannot populate the first two (generateContent sends
// neither) and ships them empty; here they are real upstream values and must not
// be dropped, because OpenAI clients key off the response id.
func (o *Client) TranslateResponse(status int, body []byte) (*provider.ChatResponse, error) {
	if status < 200 || status > 299 {
		return nil, &provider.UpstreamError{Status: status, Body: errorEnvelope(status, body)}
	}

	var out provider.ChatResponse
	// Convert to JSON
	if err := json.Unmarshal(body, &out); err != nil {
		return nil, fmt.Errorf("openai-compat: decode response: %w", err)
	}
	// Defensive default: an upstream that omits the discriminator still yields a
	// response a strict OpenAI client will accept.
	if out.Object == "" {
		out.Object = "chat.completion"
	}
	return &out, nil
}

// errorEnvelope reuses the upstream's own error envelope, which is already the
// shape LLMGuard returns to clients, so the vendor's message, type and code
// survive verbatim.
//
// It falls back to the raw body when the response is not that JSON — an
// authenticating gateway or load balancer in front of the provider can answer
// with HTML or a bare string, and swallowing that would leave the caller with a
// status and no explanation.
func errorEnvelope(status int, body []byte) provider.ErrorEnvelope {
	var env provider.ErrorEnvelope
	if err := json.Unmarshal(body, &env); err == nil && env.Error.Message != "" {
		if env.Error.Type == "" {
			env.Error.Type = provider.ErrorType(status)
		}
		return env
	}
	msg := strings.TrimSpace(string(body))
	if msg == "" {
		msg = fmt.Sprintf("upstream returned status %d", status)
	}
	return provider.NewErrorEnvelope(msg, provider.ErrorType(status))
}

// --- translation: streaming ---

// TranslateStreamChunk implements provider.Provider. `raw` is the payload of one
// SSE `data:` line; the proxy strips that prefix and the `[DONE]` sentinel before
// calling, and both are tolerated again here so the adapter is safe to drive
// directly from a raw stream.
//
// The frame is already an OpenAI chunk, so it decodes straight into StreamChunk.
func (o *Client) TranslateStreamChunk(req *provider.ChatRequest, raw []byte) ([]provider.StreamChunk, error) {
	payload := bytes.TrimSpace(raw)
	if rest, found := bytes.CutPrefix(payload, []byte("data:")); found {
		payload = bytes.TrimSpace(rest)
	}
	if len(payload) == 0 || bytes.Equal(payload, []byte("[DONE]")) {
		return nil, nil
	}

	var chunk provider.StreamChunk
	if err := json.Unmarshal(payload, &chunk); err != nil {
		return nil, fmt.Errorf("openai-compat: decode stream chunk: %w", err)
	}
	if chunk.Object == "" {
		chunk.Object = "chat.completion.chunk"
	}
	if chunk.Model == "" && req != nil {
		chunk.Model = req.Model
	}

	// A frame with neither choices nor usage is metadata only and yields nothing
	// client-visible. The usage check matters: OpenAI's final usage frame carries
	// an EMPTY choices array, and dropping it would lose token accounting on the
	// streaming path.
	if len(chunk.Choices) == 0 && chunk.Usage == nil {
		return nil, nil
	}
	return []provider.StreamChunk{chunk}, nil
}
