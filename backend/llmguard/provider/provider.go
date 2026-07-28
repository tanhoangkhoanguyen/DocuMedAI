package provider

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync"
)

// Provider adapts LLMGuard's normalized model to one vendor's native API.
//
// Everything vendor-specific lives behind this interface: URL construction,
// auth, request/response JSON, SSE framing. Adding a vendor means implementing
// Provider and registering it — core and clients are untouched.
type Provider interface {
	// Name is the registry key, e.g. "vertex".
	Name() string

	// BuildRequest translates a normalized request into a ready-to-send HTTP
	// request, including the URL and any auth headers. It is called once per
	// attempt, so a token refreshed between retries is picked up.
	BuildRequest(ctx context.Context, req *ChatRequest) (*http.Request, error)

	// TranslateResponse converts a non-streaming native response body into the
	// normalized form. A non-2xx status must produce an error carrying the
	// vendor's message.
	TranslateResponse(status int, body []byte) (*ChatResponse, error)

	// TranslateStreamChunk converts ONE native SSE data payload into zero or
	// more normalized chunks. Zero is legal — some frames carry only metadata.
	TranslateStreamChunk(req *ChatRequest, raw []byte) ([]StreamChunk, error)
}

// UpstreamError is a non-2xx response from a provider. Core inspects Status to
// decide whether to retry, and passes Body through to the caller.
type UpstreamError struct {
	Status int
	Body   ErrorEnvelope
}

func (e *UpstreamError) Error() string {
	return fmt.Sprintf("upstream %d: %s", e.Status, e.Body.Error.Message)
}

// --- Registry ---

var (
	mu        sync.RWMutex
	registry  = map[string]Provider{}
	modelRule []rule
)

type rule struct {
	prefix   string
	provider string
}

// Register adds a provider under its Name. Called from adapter constructors at
// startup; panics on a duplicate because that is a wiring bug, not input.
func Register(p Provider) {
	mu.Lock()
	defer mu.Unlock()
	if _, dup := registry[p.Name()]; dup {
		panic("provider already registered: " + p.Name())
	}
	registry[p.Name()] = p
}

// RouteModel maps a model-name prefix to a provider name, e.g. "gemini-" →
// "vertex". Longer prefixes are matched first so specific rules beat general
// ones regardless of registration order.
func RouteModel(prefix, providerName string) {
	mu.Lock()
	defer mu.Unlock()
	modelRule = append(modelRule, rule{prefix: prefix, provider: providerName})
	for i := len(modelRule) - 1; i > 0; i-- {
		if len(modelRule[i].prefix) > len(modelRule[i-1].prefix) {
			modelRule[i], modelRule[i-1] = modelRule[i-1], modelRule[i]
			continue
		}
		break
	}
}

// For resolves the provider that should serve a model. It tries the routing
// rules first, then falls back to fallbackName (the configured default) so an
// unrecognized-but-valid model still reaches a provider rather than 400-ing.
func For(model, fallbackName string) (Provider, error) {
	mu.RLock()
	defer mu.RUnlock()

	for _, r := range modelRule {
		if strings.HasPrefix(model, r.prefix) {
			if p, ok := registry[r.provider]; ok {
				return p, nil
			}
		}
	}
	if p, ok := registry[fallbackName]; ok {
		return p, nil
	}
	return nil, fmt.Errorf("no provider for model %q", model)
}

// Reset clears the registry. Tests only.
func Reset() {
	mu.Lock()
	defer mu.Unlock()
	registry = map[string]Provider{}
	modelRule = nil
}
