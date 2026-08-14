package gateway

// Characterization: the provider label on request-scoped metrics.
// Harness and thresholds live in harness_test.go.

import (
	"net/http"
	"strings"
	"testing"

	"documedai/llmguard/internal/testutil"
	"documedai/llmguard/mockupstream"
	"documedai/llmguard/provider"
	"documedai/llmguard/provider/vertex"
)

// routedProvider is a mockProvider under a second name, so a request can be
// routed to an adapter other than the one the harness registers by default.
type routedProvider struct{ *mockProvider }

func (p *routedProvider) Name() string { return "routed" }

// TestProviderLabelUsesResolvedAdapter proves the label names the adapter that
// actually served the request.
//
// With two adapters registered, labelling from anything other than the resolved
// provider — a config default, the first registration, a constant — would still
// produce a plausible series while attributing traffic to the wrong upstream.
// Two adapters is the only arrangement in which that mistake is visible.
func TestProviderLabelUsesResolvedAdapter(t *testing.T) {
	h := newHarness(t, realDefaults(), mockupstream.DefaultConfig(), nil)

	// The harness registered "mock" and enabled gemini-2.5-flash on it. Add a
	// second adapter and enable the SAME model on it too — the case the pair key
	// exists for, and the one where a mislabelled series is visible.
	provider.Register(&routedProvider{mockProvider: &mockProvider{
		base: h.up.server.URL, inner: &vertex.Client{},
	}})
	provider.SetRoutes([]provider.Route{
		{Provider: "mock", Model: "gemini-2.5-flash"},
		{Provider: "routed", Model: "gemini-2.5-flash"},
	})

	body := `{"provider":"routed","model":"gemini-2.5-flash",` +
		`"messages":[{"role":"user","content":"label me"}]}`
	rec := h.do(t, body, nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}

	if got := testutil.LabeledCounterValue(
		t, h.metrics.requests, "routed", "gemini-2.5-flash", "2xx",
	); got != 1 {
		t.Errorf("requests{provider=\"routed\"} = %v, want 1 — "+
			"the label must come from the resolved adapter", got)
	}
	// And nothing was attributed to the other adapter serving the same model.
	if got := testutil.LabeledCounterValue(
		t, h.metrics.requests, "mock", "gemini-2.5-flash", "2xx",
	); got != 0 {
		t.Errorf("requests{provider=\"mock\"} = %v, want 0", got)
	}
}

// TestUnlistedModelIsRejected is the allowlist's contract at the HTTP boundary:
// a model the operator did not enable never reaches an upstream, and the caller
// is told which models exist.
func TestUnlistedModelIsRejected(t *testing.T) {
	h := newHarness(t, realDefaults(), mockupstream.DefaultConfig(), nil)

	rec := h.do(t, chatBody("gpt-4-turbo", "not enabled", false), nil)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400\nbody: %s", rec.Code, rec.Body.String())
	}
	env := decodeError(t, rec)
	if !strings.Contains(env.Error.Message, "gpt-4-turbo") {
		t.Errorf("message = %q, want it to name the rejected model", env.Error.Message)
	}
	if !strings.Contains(env.Error.Message, "mock/gemini-2.5-flash") {
		t.Errorf("message = %q, want it to list the enabled routes", env.Error.Message)
	}
	// The point of the allowlist: no upstream call at all.
	if h.up.Hits() != 0 {
		t.Errorf("upstream hits = %d, want 0 — an unlisted model must not be dispatched", h.up.Hits())
	}
}

// TestMissingProviderIsRejected pins that provider is required with no default.
//
// One model can be served by several upstreams, so choosing one for the caller
// would dispatch to an upstream they never picked — a silent wrong answer rather
// than an error they can fix.
func TestMissingProviderIsRejected(t *testing.T) {
	h := newHarness(t, realDefaults(), mockupstream.DefaultConfig(), nil)

	// A model that IS enabled, so only the absent provider can be what fails.
	rec := h.do(t, `{"model":"gemini-2.5-flash",`+
		`"messages":[{"role":"user","content":"hi"}]}`, nil)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400\nbody: %s", rec.Code, rec.Body.String())
	}
	// The exact message, not a substring: without the explicit check an absent
	// provider still 400s — route {"", model} is simply not in the allowlist — and
	// that error names "provider" too. Only the wording distinguishes "you left a
	// field out" from "that pair is not enabled", and the first is the one a
	// caller can act on.
	want := "field 'provider' is required — it names which upstream serves this model"
	if env := decodeError(t, rec); env.Error.Message != want {
		t.Errorf("message = %q,\nwant %q", env.Error.Message, want)
	}
	if h.up.Hits() != 0 {
		t.Errorf("upstream hits = %d, want 0", h.up.Hits())
	}
}

// TestProviderLabelUnknownBeforeResolution pins the label on requests rejected
// before an adapter exists.
//
// These still consume a response and still belong in requests_total, so they
// need some provider value. Dropping the observation would understate the error
// rate; reusing the configured default would blame an adapter that never ran.
func TestProviderLabelUnknownBeforeResolution(t *testing.T) {
	h := newHarness(t, realDefaults(), mockupstream.DefaultConfig(), nil)

	rec := h.do(t, `{"messages":[{"role":"user","content":"hi"}]}`, nil) // no model field
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400\nbody: %s", rec.Code, rec.Body.String())
	}

	if got := testutil.LabeledCounterValue(
		t, h.metrics.requests, providerUnknown, "unknown", "4xx",
	); got != 1 {
		t.Errorf("requests{provider=%q,model=\"unknown\"} = %v, want 1", providerUnknown, got)
	}
	if h.up.Hits() != 0 {
		t.Errorf("upstream hits = %d, want 0 — rejected before dispatch", h.up.Hits())
	}
}
