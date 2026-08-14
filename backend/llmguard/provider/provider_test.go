package provider

import (
	"context"
	"errors"
	"net/http"
	"testing"
)

// stubProvider is a Provider that only carries a name.
//
// The registry keys on Name() and never calls the translation methods, so a stub
// exercises it exactly as a real adapter would. Deliberately NOT a real adapter:
// those live in provider/openai and provider/vertex, which import this package —
// reaching for one here would make the dependency circular.
type stubProvider struct{ name string }

func (s *stubProvider) Name() string { return s.name }

func (s *stubProvider) BuildRequest(context.Context, *ChatRequest) (*http.Request, error) {
	return nil, errors.New("stubProvider: BuildRequest is not implemented")
}

func (s *stubProvider) TranslateResponse(int, []byte) (*ChatResponse, error) {
	return nil, errors.New("stubProvider: TranslateResponse is not implemented")
}

func (s *stubProvider) TranslateStreamChunk(*ChatRequest, []byte) ([]StreamChunk, error) {
	return nil, errors.New("stubProvider: TranslateStreamChunk is not implemented")
}

// --- error vocabulary ---

func TestErrorTypeMapping(t *testing.T) {
	cases := map[int]string{
		429: "rate_limit",
		401: "auth_error",
		403: "auth_error",
		500: "upstream_error",
		400: "invalid_request_error",
	}
	for status, want := range cases {
		if got := ErrorType(status); got != want {
			t.Errorf("ErrorType(%d) = %q, want %q", status, got, want)
		}
	}
}

// --- registry ---

func TestRegistryRoutesExactly(t *testing.T) {
	Reset()
	defer Reset()

	Register(&stubProvider{name: "vertex"})
	SetRoutes([]Route{{Provider: "vertex", Model: "gemini-2.5-flash"}})

	got, err := For(Route{Provider: "vertex", Model: "gemini-2.5-flash"})
	if err != nil {
		t.Fatalf("For: %v", err)
	}
	if got.Name() != "vertex" {
		t.Errorf("provider = %q, want vertex", got.Name())
	}
}

// TestRegistryRoutesPerProvider is why the key is a pair rather than a model:
// one model served by two upstreams must resolve to two different adapters.
//
// Keyed on the model alone these two entries collide and one silently wins,
// sending traffic to an upstream the caller did not ask for.
func TestRegistryRoutesPerProvider(t *testing.T) {
	Reset()
	defer Reset()

	Register(&stubProvider{name: "vertex"})
	Register(&stubProvider{name: "openrouter"})

	SetRoutes([]Route{
		{Provider: "vertex", Model: "gemini-2.5-flash"},
		{Provider: "openrouter", Model: "gemini-2.5-flash"},
	})

	for _, want := range []string{"vertex", "openrouter"} {
		got, err := For(Route{Provider: want, Model: "gemini-2.5-flash"})
		if err != nil {
			t.Fatalf("For(%s): %v", want, err)
		}
		if got.Name() != want {
			t.Errorf("provider = %q, want %q — the two routes must not collide",
				got.Name(), want)
		}
	}
}

// TestRegisterRejectsDuplicateName pins the panic that the "register under the
// configured name" bug hit: two instances of one adapter type whose Name() is a
// constant collide on the registry key.
//
// The panic is deliberate — a duplicate is a wiring bug, not input — so it is
// pinned rather than left as incidental behavior.
func TestRegisterRejectsDuplicateName(t *testing.T) {
	Reset()
	defer Reset()

	Register(&stubProvider{name: "vertex"})

	defer func() {
		if recover() == nil {
			t.Error("registering a duplicate name must panic")
		}
	}()
	Register(&stubProvider{name: "vertex"})
}

// TestRegistryRejectsUnlistedModel is the allowlist's whole purpose: a model the
// operator did not enable must not reach any provider.
//
// The error is typed so proxy.go can answer 400 for this while still answering
// 500 for a wiring failure — the caller can fix one and not the other.
func TestRegistryRejectsUnlistedModel(t *testing.T) {
	Reset()
	defer Reset()

	Register(&stubProvider{name: "vertex"})
	SetRoutes([]Route{{Provider: "vertex", Model: "gemini-2.5-flash"}})

	// A prefix of a listed model, which a prefix-matching registry would serve.
	unlisted := Route{Provider: "vertex", Model: "gemini-2.5-flash-preview"}
	_, err := For(unlisted)
	var unknown *UnknownModelError
	if !errors.As(err, &unknown) {
		t.Fatalf("err = %v (%T), want *UnknownModelError", err, err)
	}
	if unknown.Route != unlisted {
		t.Errorf("Route = %v, want %v", unknown.Route, unlisted)
	}
}

// TestRegistryRejectsListedModelOnWrongProvider pins that BOTH halves are
// checked. Enabling a model on one upstream must not enable it on every other
// upstream the operator happens to have declared.
func TestRegistryRejectsListedModelOnWrongProvider(t *testing.T) {
	Reset()
	defer Reset()

	Register(&stubProvider{name: "vertex"})
	Register(&stubProvider{name: "openrouter"})

	// Only the vertex route is granted.
	SetRoutes([]Route{{Provider: "vertex", Model: "gemini-2.5-flash"}})

	// Same model, registered provider, but the pair was never allowed.
	_, err := For(Route{Provider: "openrouter", Model: "gemini-2.5-flash"})
	var unknown *UnknownModelError
	if !errors.As(err, &unknown) {
		t.Fatalf("err = %v (%T), want *UnknownModelError", err, err)
	}
}

// TestRegistryUnregisteredProviderIsNotAClientError separates the two failures a
// caller must be able to tell apart: an unlisted model is the client's mistake,
// a route pointing at a provider that never registered is ours.
func TestRegistryUnregisteredProviderIsNotAClientError(t *testing.T) {
	Reset()
	defer Reset()

	SetRoutes([]Route{{Provider: "never-registered", Model: "m"}})

	_, err := For(Route{Provider: "never-registered", Model: "m"})
	if err == nil {
		t.Fatal("expected an error")
	}
	var unknown *UnknownModelError
	if errors.As(err, &unknown) {
		t.Error("a loader bug must not be reported as an unknown model")
	}
}

// TestSetRoutesReplaces pins that routing is a wholesale swap, not an append: a
// model dropped from the config must stop resolving.
func TestSetRoutesReplaces(t *testing.T) {
	Reset()
	defer Reset()

	Register(&stubProvider{name: "vertex"})
	SetRoutes([]Route{{Provider: "vertex", Model: "old"}})
	SetRoutes([]Route{{Provider: "vertex", Model: "new"}})

	if _, err := For(Route{Provider: "vertex", Model: "old"}); err == nil {
		t.Error("a model removed from the config must no longer resolve")
	}
	if _, err := For(Route{Provider: "vertex", Model: "new"}); err != nil {
		t.Errorf("For(new): %v", err)
	}
}

func TestEnabledRoutesIsSorted(t *testing.T) {
	Reset()
	defer Reset()

	Register(&stubProvider{name: "vertex"})
	SetRoutes([]Route{
		{Provider: "vertex", Model: "zeta"},
		{Provider: "vertex", Model: "alpha"},
		{Provider: "vertex", Model: "mid"},
	})

	// Sorted, because this list is quoted back in a 400 body and map iteration
	// order would make that response differ between identical calls.
	got := EnabledRoutes()
	want := []string{"vertex/alpha", "vertex/mid", "vertex/zeta"}
	if len(got) != len(want) || got[0] != want[0] || got[1] != want[1] || got[2] != want[2] {
		t.Errorf("EnabledRoutes() = %v, want %v", got, want)
	}
}
