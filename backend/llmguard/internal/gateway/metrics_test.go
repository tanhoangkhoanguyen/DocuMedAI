package gateway

// Characterization: the provider label on request-scoped metrics.
// Harness and thresholds live in harness_test.go.

import (
	"net/http"
	"testing"

	"documedai/llmguard/internal/testutil"
	"documedai/llmguard/mockupstream"
	"documedai/llmguard/provider"
)

// routedProvider is a mockProvider under a different name, so a request can be
// routed to an adapter whose Name() differs from cfg.Provider.
type routedProvider struct{ *mockProvider }

func (p *routedProvider) Name() string { return "routed" }

// TestProviderLabelUsesResolvedAdapter proves the label comes from the adapter
// that actually served the request, not from cfg.Provider.
//
// Every other test in the suite is blind to the difference: the harness sets
// cfg.Provider = "mock" AND registers an adapter named "mock", so labelling with
// either produces the same series. Once routing is config-driven the two diverge,
// and cfg.Provider would attribute every request to the configured default —
// metrics that look healthy while pointing at the wrong upstream.
//
// This breaks the tie by routing the model to a SECOND adapter named "routed"
// while cfg.Provider stays "mock". cfg.Provider cannot simply be changed to a
// bogus name: provider.For takes it as the fallback registry key, so resolution
// would fail with a 400 before any label is recorded.
func TestProviderLabelUsesResolvedAdapter(t *testing.T) {
	h := newHarness(t, realDefaults(), mockupstream.DefaultConfig(), nil)

	// newHarness registered "mock" and left cfg.Provider = "mock". Add a second
	// adapter and a rule that wins over the fallback for this model.
	provider.Register(&routedProvider{mockProvider: &mockProvider{
		base: h.up.server.URL, inner: &provider.Vertex{},
	}})
	provider.RouteModel("gemini-", "routed")

	rec := h.do(t, chatBody("gemini-2.5-flash", "label me", false), nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200\nbody: %s", rec.Code, rec.Body.String())
	}

	if got := testutil.LabeledCounterValue(
		t, h.metrics.requests, "routed", "gemini-2.5-flash", "2xx",
	); got != 1 {
		t.Errorf("requests{provider=\"routed\"} = %v, want 1 — "+
			"the label must come from prov.Name(), not cfg.Provider", got)
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
