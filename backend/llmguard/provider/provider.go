package provider

import (
	"context"
	"fmt"
	"net/http"
	"sort"
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

	// RetryAfter is the vendor's Retry-After header, verbatim, when it sent
	// one. It rides on the error because that is the only thing that survives
	// the breaker and deduper on the failure path — the *upstreamResult holding
	// the response headers is discarded there. Core forwards it to the client
	// so a caller can honor the provider's pacing instead of guessing.
	//
	// Empty when absent. Not parsed here: core needs the raw value to pass on,
	// and both RFC 9110 forms (delta-seconds and HTTP-date) are valid to echo.
	RetryAfter string
}

func (e *UpstreamError) Error() string {
	return fmt.Sprintf("upstream %d: %s", e.Status, e.Body.Error.Message)
}

// --- Registry ---

var (
	mu       sync.RWMutex
	registry = map[string]Provider{}
	// allowed (provider, model) pairs. The value is empty — this is a set, and
	// the provider name is already the registry key, so nothing else is needed.
	routes = map[Route]struct{}{}
)

// Route is one entry of the allowlist: a model, and the upstream serving it.
//
// A pair rather than a single string because one model can be served by several
// upstreams, so the model alone does not identify a route. Comparable, so it is
// usable as a map key directly — no separator to escape, and no ambiguity when a
// vendor puts a slash in the model name itself ("openai/gpt-4o-mini").
type Route struct {
	Provider string
	Model    string
}

func (r Route) String() string { return r.Provider + "/" + r.Model }

// UnknownModelError reports a (provider, model) pair the allowlist does not
// carry.
//
// A named type so the caller can tell a client mistake — asking for a pair the
// operator never enabled, which deserves a 400 — from an internal wiring failure,
// which does not.
type UnknownModelError struct {
	Route Route
}

func (e *UnknownModelError) Error() string {
	return fmt.Sprintf("model %q is not enabled on provider %q", e.Route.Model, e.Route.Provider)
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

// SetRoutes replaces the allowlist wholesale.
//
// One atomic swap rather than an append-per-entry, so resolution never depends
// on the order rules were added and a reload cannot leave a half-applied table.
func SetRoutes(allowed []Route) {
	mu.Lock()
	defer mu.Unlock()
	routes = make(map[Route]struct{}, len(allowed))
	for _, r := range allowed {
		routes[r] = struct{}{}
	}
}

// For resolves the adapter serving one route. Exact match only: there is no
// prefix matching and no fallback.
//
// An allowlist whose entries are prefixes is not an allowlist, and a fallback
// would serve a model the operator never enabled — which is exactly the request
// the allowlist exists to refuse. Both halves are checked together: a provider
// that serves gemini-2.5-flash does not thereby serve every model, so matching
// on the provider alone would grant more than the operator wrote.
func For(route Route) (Provider, error) {
	mu.RLock()
	defer mu.RUnlock()

	if _, ok := routes[route]; !ok {
		return nil, &UnknownModelError{Route: route}
	}
	p, ok := registry[route.Provider]
	if !ok {
		// An allowed route naming a provider that was never registered is a loader
		// bug, not a bad request, so deliberately NOT an UnknownModelError.
		return nil, fmt.Errorf("route %s names provider %q, which is not registered",
			route, route.Provider)
	}
	return p, nil
}

// EnabledRoutes lists every allowed route as "provider/model", sorted.
//
// Sorted because this is quoted back in the 400 body for an unknown model, and a
// map's iteration order would make that response differ between identical calls.
// Rendered as one string per route so the caller can join them for a message
// without knowing the pair's shape.
func EnabledRoutes() []string {
	mu.RLock()
	defer mu.RUnlock()

	out := make([]string, 0, len(routes))
	for r := range routes {
		out = append(out, r.String())
	}
	sort.Strings(out)
	return out
}

// Reset clears the registry. Tests only.
func Reset() {
	mu.Lock()
	defer mu.Unlock()
	registry = map[string]Provider{}
	routes = map[Route]struct{}{}
}
