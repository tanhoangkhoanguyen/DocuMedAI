package gateway

// Characterization: the model allowlist parser.
//
// Every case here is a config that must be REJECTED. The allowlist decides which
// models are callable, so validation failing open is the failure mode that
// matters: a config that loads but is wrong produces a gateway routing traffic
// somewhere the operator did not intend, or 400ing everything while looking
// healthy. The happy path is covered once, as the baseline those cases mutate.

import (
	"os"
	"path/filepath"
	"strings"

	"documedai/llmguard/provider"
	"testing"
)

// writeConfig writes a config file and returns its path.
func writeConfig(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatalf("write config: %v", err)
	}
	return path
}

const validConfig = `
version: 1
providers:
  - name: vertex
    type: vertex
    project_env: GOOGLE_CLOUD_PROJECT
    location: us-central1
  - name: openrouter
    type: openai-compat
    base_url: https://openrouter.ai/api/v1
    api_key_env: TEST_OPENROUTER_KEY
model_list:
  - model_name: gemini-2.5-flash
    provider: vertex
    pricing: {input_per_1k: 0.000075, output_per_1k: 0.0003}
  - model_name: gpt-4o-mini
    provider: openrouter
    upstream_model: openai/gpt-4o-mini
`

// TestLoadModelConfig covers the happy path end to end: both provider types
// parse, and the fields routing will read carry the values from the file.
func TestLoadModelConfig(t *testing.T) {
	t.Setenv("TEST_OPENROUTER_KEY", "sk-test")

	cfg, err := loadModelConfig(writeConfig(t, validConfig))
	if err != nil {
		t.Fatalf("loadModelConfig: %v", err)
	}

	want := []provider.Route{
		{Provider: "vertex", Model: "gemini-2.5-flash"},
		{Provider: "openrouter", Model: "gpt-4o-mini"},
	}
	if got := cfg.EnabledRoutes(); len(got) != 2 || got[0] != want[0] || got[1] != want[1] {
		t.Errorf("EnabledRoutes() = %v, want %v", got, want)
	}

	// upstream_model overrides the identifier sent upstream; absent means "same".
	if got := cfg.ModelList[1].Upstream(); got != "openai/gpt-4o-mini" {
		t.Errorf("Upstream() = %q, want openai/gpt-4o-mini", got)
	}
	if got := cfg.ModelList[0].Upstream(); got != "gemini-2.5-flash" {
		t.Errorf("Upstream() = %q, want the model_name", got)
	}

	if _, ok := cfg.ProviderByName("openrouter"); !ok {
		t.Error("ProviderByName(openrouter) not found")
	}
	if _, ok := cfg.ProviderByName("nope"); ok {
		t.Error("ProviderByName(nope) must not resolve")
	}
}

// TestLoadModelConfigAllowsOneModelOnManyProviders pins the reason the allowlist
// is keyed on a pair: the same model served by two upstreams is a valid config,
// not a duplicate.
//
// A duplicate check on model_name alone would reject this, forcing an operator to
// invent distinct aliases for what is genuinely one model.
func TestLoadModelConfigAllowsOneModelOnManyProviders(t *testing.T) {
	t.Setenv("TEST_OPENROUTER_KEY", "sk-test")

	cfg, err := loadModelConfig(writeConfig(t, `
version: 1
providers:
  - {name: vertex, type: vertex, project_env: GOOGLE_CLOUD_PROJECT}
  - {name: openrouter, type: openai-compat, base_url: https://openrouter.ai/api/v1, api_key_env: TEST_OPENROUTER_KEY}
model_list:
  - {model_name: gemini-2.5-flash, provider: vertex}
  - {model_name: gemini-2.5-flash, provider: openrouter}`))
	if err != nil {
		t.Fatalf("one model on two providers must be accepted: %v", err)
	}

	got := cfg.EnabledRoutes()
	want := []provider.Route{
		{Provider: "vertex", Model: "gemini-2.5-flash"},
		{Provider: "openrouter", Model: "gemini-2.5-flash"},
	}
	if len(got) != 2 || got[0] != want[0] || got[1] != want[1] {
		t.Errorf("EnabledRoutes() = %v, want %v — both routes must survive", got, want)
	}
}

// TestLoadModelConfigRejects is the core of this file: each case is a config
// that must not boot, and the reason it must not.
func TestLoadModelConfigRejects(t *testing.T) {
	tests := []struct {
		name    string
		body    string
		wantMsg string
	}{
		{
			// Silently ignoring an unknown key means "base_ur:" leaves BaseURL
			// empty and the operator debugs a connection error, not a typo.
			name: "misspelled key",
			body: `
version: 1
providers: [{name: p, type: openai-compat, base_ur: https://x.test/v1, api_key_env: TEST_KEY}]
model_list: [{model_name: m, provider: p}]`,
			// Asserts the DECODER rejected it, not a downstream empty-field
			// check: with KnownFields off the typo is ignored and base_url is
			// simply empty, which "required" would also report.
			wantMsg: "field base_ur not found",
		},
		{
			// An empty allowlist serves nothing. Booting on it yields a gateway
			// that 400s every request while its health check passes.
			name:    "empty model_list",
			body:    "version: 1\nproviders: []\nmodel_list: []",
			wantMsg: "model_list must not be empty",
		},
		{
			// The same PAIR twice: routing would depend on file order, which no
			// reader expects. The same model under two providers is legal and
			// covered by TestLoadModelConfigAllowsOneModelOnManyProviders.
			name: "duplicate model_name on one provider",
			body: `
version: 1
providers: [{name: p, type: vertex, project_env: GOOGLE_CLOUD_PROJECT}]
model_list:
  - {model_name: m, provider: p}
  - {model_name: m, provider: p}`,
			wantMsg: "duplicate model_name",
		},
		{
			// A model pointing at a provider that does not exist is unroutable;
			// caught at startup rather than on the first request for it.
			name: "model references unknown provider",
			body: `
version: 1
providers: [{name: p, type: vertex, project_env: GOOGLE_CLOUD_PROJECT}]
model_list: [{model_name: m, provider: typo}]`,
			wantMsg: `provider "typo" is not declared`,
		},
		{
			// Two providers sharing a name make the second unreachable and give
			// the breaker and metrics an ambiguous key.
			name: "duplicate provider name",
			body: `
version: 1
providers:
  - {name: p, type: vertex, project_env: GOOGLE_CLOUD_PROJECT}
  - {name: p, type: vertex, project_env: GOOGLE_CLOUD_PROJECT}
model_list: [{model_name: m, provider: p}]`,
			wantMsg: "duplicate provider name",
		},
		{
			name: "unknown provider type",
			body: `
version: 1
providers: [{name: p, type: anthropic}]
model_list: [{model_name: m, provider: p}]`,
			wantMsg: `unknown type "anthropic"`,
		},
		{
			// A settings block that belongs to another type reads as if it is in
			// effect. Rejecting it beats ignoring it.
			name: "base_url on a vertex provider",
			body: `
version: 1
providers: [{name: p, type: vertex, project_env: X, base_url: https://x.test/v1}]
model_list: [{model_name: m, provider: p}]`,
			wantMsg: "base_url is not valid for type vertex",
		},
		{
			// A declared-but-unset key is the same outage as a wrong key, except
			// it only surfaces on the first upstream call.
			name: "api_key_env names an unset variable",
			body: `
version: 1
providers: [{name: p, type: openai-compat, base_url: https://x.test/v1, api_key_env: DEFINITELY_UNSET_KEY}]
model_list: [{model_name: m, provider: p}]`,
			wantMsg: "DEFINITELY_UNSET_KEY is empty",
		},
		{
			// Not a URL LLMGuard can call.
			name: "base_url without a scheme",
			body: `
version: 1
providers: [{name: p, type: openai-compat, base_url: openrouter.ai/api/v1, api_key_env: TEST_KEY}]
model_list: [{model_name: m, provider: p}]`,
			wantMsg: "base_url must be http or https",
		},
		{
			// Guards the file format itself: a future version 2 must not be read
			// with version 1 semantics.
			name: "wrong version",
			body: `
version: 2
providers: [{name: p, type: vertex, project_env: GOOGLE_CLOUD_PROJECT}]
model_list: [{model_name: m, provider: p}]`,
			wantMsg: "version must be 1",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("TEST_KEY", "sk-test")

			_, err := loadModelConfig(writeConfig(t, tc.body))
			if err == nil {
				t.Fatal("config was accepted; it must be rejected")
			}
			if !strings.Contains(err.Error(), tc.wantMsg) {
				t.Errorf("error = %v\nwant it to mention %q", err, tc.wantMsg)
			}
		})
	}
}

// TestLoadModelConfigReportsEveryProblem pins that validation accumulates.
//
// Returning only the first problem turns one bad edit into a restart-per-fix
// loop, which is how a five-minute config change becomes an afternoon.
func TestLoadModelConfigReportsEveryProblem(t *testing.T) {
	_, err := loadModelConfig(writeConfig(t, `
version: 3
providers: [{name: p, type: vertex}]
model_list: [{model_name: m, provider: other}]`))
	if err == nil {
		t.Fatal("config was accepted; it must be rejected")
	}

	for _, want := range []string{
		"version must be 1",
		"project_env is required",
		`provider "other" is not declared`,
	} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error is missing %q\ngot: %v", want, err)
		}
	}
}

// TestLoadModelConfigMissingFile pins that an absent file is an error rather
// than an empty config. With a strict allowlist, "no config" and "allow nothing"
// are the same thing — and a gateway that starts and refuses every request is
// worse than one that refuses to start.
func TestLoadModelConfigMissingFile(t *testing.T) {
	if _, err := loadModelConfig(filepath.Join(t.TempDir(), "absent.yaml")); err == nil {
		t.Fatal("a missing config file must be an error")
	}
}
