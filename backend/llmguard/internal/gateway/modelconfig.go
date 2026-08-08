package gateway

// The model allowlist: which models LLMGuard will serve, and which upstream
// each one goes to.
//
// This file only PARSES and VALIDATES the file. Nothing here routes a request —
// wiring it into provider resolution is a separate step, so this can be reviewed
// as a schema on its own.
//
// Two levels (providers + model_list) rather than one flat list. Flattening
// would repeat base_url and api_key_env on every model that shares an upstream,
// and a typo in one copy would silently split routing in two. The provider name
// is also the natural key for a per-provider circuit breaker.
//
// The allowlist is STRICT and matches model names exactly. There is no lenient
// mode and no prefix matching — an allowlist whose entries are prefixes is not
// an allowlist. A model that is not listed cannot be called, which makes the
// file the single place an operator grants access.

import (
	"errors"
	"fmt"
	"net/url"
	"os"
	"strings"

	"gopkg.in/yaml.v3"
)

// Provider type names accepted in `type:`. These are adapter KINDS, not
// instance names — several instances of one kind can coexist under different
// names (two openai-compat upstreams, say).
const (
	providerTypeVertex       = "vertex"
	providerTypeOpenAICompat = "openai-compat"
)

// ModelConfig is a parsed, validated config.yaml.
type ModelConfig struct {
	Version   int              `yaml:"version"`
	Providers []ProviderConfig `yaml:"providers"`
	ModelList []ModelEntry     `yaml:"model_list"`
}

// ProviderConfig declares one upstream instance.
type ProviderConfig struct {
	// Name is how model entries refer to this upstream, and the label that
	// appears on its metrics and circuit breaker. Unique across the file.
	Name string `yaml:"name"`
	// Type selects the adapter kind: vertex | openai-compat.
	Type string `yaml:"type"`

	// ProjectEnv/Location configure a vertex provider. ProjectEnv holds the NAME
	// of an environment variable, never a value — the config file is committed,
	// so it must not be able to carry a credential even by accident.
	ProjectEnv string `yaml:"project_env"`
	Location   string `yaml:"location"`

	// BaseURL/APIKeyEnv configure an openai-compat provider. BaseURL includes
	// the vendor's version prefix and omits /chat/completions; APIKeyEnv is again
	// an env var NAME.
	BaseURL   string `yaml:"base_url"`
	APIKeyEnv string `yaml:"api_key_env"`
}

// ModelEntry allows exactly one model and binds it to a provider.
type ModelEntry struct {
	// ModelName is what a client sends in the `model` field. Matched exactly.
	ModelName string `yaml:"model_name"`
	// Provider names an entry in Providers.
	Provider string `yaml:"provider"`
	// UpstreamModel overrides the identifier sent upstream when the vendor spells
	// it differently ("openai/gpt-4o-mini" on OpenRouter). Empty means "same as
	// ModelName".
	UpstreamModel string `yaml:"upstream_model"`
	// Pricing is recorded so a later consumer can attribute spend. LLMGuard is a
	// reliability gateway and does nothing with these numbers itself — no
	// budgets, no cost metrics, no enforcement.
	Pricing *Pricing `yaml:"pricing"`
}

// Pricing is per-1k-token rates as published by the vendor. Stored data only.
type Pricing struct {
	InputPer1k  float64 `yaml:"input_per_1k"`
	OutputPer1k float64 `yaml:"output_per_1k"`
}

// Upstream returns the model identifier to send to the provider.
func (m ModelEntry) Upstream() string {
	if m.UpstreamModel != "" {
		return m.UpstreamModel
	}
	return m.ModelName
}

// EnabledModels lists every allowed model name, in file order.
//
// File order, not sorted: it is what an operator sees in config.yaml, and the
// 400 body for an unknown model quotes this list back.
func (c *ModelConfig) EnabledModels() []string {
	out := make([]string, 0, len(c.ModelList))
	for _, m := range c.ModelList {
		out = append(out, m.ModelName)
	}
	return out
}

// ProviderByName returns a declared provider.
func (c *ModelConfig) ProviderByName(name string) (ProviderConfig, bool) {
	for _, p := range c.Providers {
		if p.Name == name {
			return p, true
		}
	}
	return ProviderConfig{}, false
}

// loadModelConfig reads and validates config.yaml.
//
// A missing file is an error, not an empty config: with a strict allowlist an
// empty config serves nothing, so booting on one would produce a gateway that
// 400s every request while looking healthy. main is expected to exit on this.
func loadModelConfig(path string) (*ModelConfig, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("config: %w", err)
	}
	defer f.Close()

	dec := yaml.NewDecoder(f)
	// A misspelled key is a silent misconfiguration otherwise: "base_ur" would
	// parse fine and leave BaseURL empty, and the operator would be debugging a
	// connection error instead of reading a typo.
	dec.KnownFields(true)

	var cfg ModelConfig
	if derr := dec.Decode(&cfg); derr != nil {
		return nil, fmt.Errorf("config %s: %w", path, derr)
	}
	if verr := cfg.validate(); verr != nil {
		return nil, fmt.Errorf("config %s: %w", path, verr)
	}
	return &cfg, nil
}

// validate accumulates every problem rather than returning the first.
//
// One bad edit often breaks several entries; reporting them one restart at a
// time turns a single fix into a guessing loop.
func (c *ModelConfig) validate() error {
	var problems []error

	if c.Version != 1 {
		problems = append(problems,
			fmt.Errorf("version must be 1, got %d", c.Version))
	}

	seenProvider := map[string]bool{}
	for i, p := range c.Providers {
		problems = append(problems, p.validate(i, seenProvider)...)
	}

	// An empty allowlist is a config that serves nothing. Treated as an error so
	// it surfaces at startup instead of as a wall of 400s.
	if len(c.ModelList) == 0 {
		problems = append(problems, errors.New("model_list must not be empty"))
	}

	seenModel := map[string]bool{}
	for i, m := range c.ModelList {
		where := fmt.Sprintf("model_list[%d]", i)

		switch {
		case m.ModelName == "":
			problems = append(problems, fmt.Errorf("%s: model_name is required", where))
		case seenModel[m.ModelName]:
			// Silently taking the first (or last) would make routing depend on
			// file order, which no reader would expect.
			problems = append(problems,
				fmt.Errorf("%s: duplicate model_name %q", where, m.ModelName))
		default:
			seenModel[m.ModelName] = true
		}

		switch {
		case m.Provider == "":
			problems = append(problems, fmt.Errorf("%s: provider is required", where))
		case !seenProvider[m.Provider]:
			problems = append(problems,
				fmt.Errorf("%s: provider %q is not declared in providers", where, m.Provider))
		}

		// Negative prices are rejected; zero is allowed and not warned about.
		// A missing price costs a later consumer some accuracy — it is not a
		// reason to refuse traffic on a reliability gateway.
		if m.Pricing != nil {
			if m.Pricing.InputPer1k < 0 || m.Pricing.OutputPer1k < 0 {
				problems = append(problems, fmt.Errorf("%s: pricing must not be negative", where))
			}
		}
	}

	return errors.Join(problems...)
}

// validate checks one provider entry and records its name in seen.
func (p ProviderConfig) validate(i int, seen map[string]bool) []error {
	where := fmt.Sprintf("providers[%d]", i)
	var problems []error

	switch {
	case p.Name == "":
		problems = append(problems, fmt.Errorf("%s: name is required", where))
	case seen[p.Name]:
		problems = append(problems, fmt.Errorf("%s: duplicate provider name %q", where, p.Name))
	default:
		seen[p.Name] = true
		where = fmt.Sprintf("providers[%d] (%s)", i, p.Name)
	}

	switch p.Type {
	case providerTypeVertex:
		if p.ProjectEnv == "" {
			problems = append(problems, fmt.Errorf("%s: project_env is required", where))
		}
		problems = append(problems, rejectFields(where, "vertex",
			field{"base_url", p.BaseURL}, field{"api_key_env", p.APIKeyEnv})...)

	case providerTypeOpenAICompat:
		problems = append(problems, p.validateOpenAICompat(where)...)
		problems = append(problems, rejectFields(where, "openai-compat",
			field{"project_env", p.ProjectEnv}, field{"location", p.Location})...)

	case "":
		problems = append(problems, fmt.Errorf("%s: type is required (%s|%s)",
			where, providerTypeVertex, providerTypeOpenAICompat))
	default:
		problems = append(problems, fmt.Errorf("%s: unknown type %q (want %s or %s)",
			where, p.Type, providerTypeVertex, providerTypeOpenAICompat))
	}

	return problems
}

func (p ProviderConfig) validateOpenAICompat(where string) []error {
	var problems []error

	switch u, err := url.Parse(p.BaseURL); {
	case p.BaseURL == "":
		problems = append(problems, fmt.Errorf("%s: base_url is required", where))
	case err != nil:
		problems = append(problems, fmt.Errorf("%s: base_url is not a URL: %w", where, err))
	case u.Scheme != "http" && u.Scheme != "https":
		problems = append(problems,
			fmt.Errorf("%s: base_url must be http or https, got %q", where, u.Scheme))
	case u.Host == "":
		problems = append(problems, fmt.Errorf("%s: base_url has no host", where))
	}

	// The env var must be NAMED and must be SET. A declared-but-unset key is the
	// same outage as a wrong key, except it only shows up on the first upstream
	// call — better to refuse to start.
	if p.APIKeyEnv == "" {
		problems = append(problems, fmt.Errorf("%s: api_key_env is required", where))
	} else if os.Getenv(p.APIKeyEnv) == "" {
		problems = append(problems,
			fmt.Errorf("%s: environment variable %s is empty", where, p.APIKeyEnv))
	}

	return problems
}

type field struct{ name, value string }

// rejectFields reports settings that belong to a different provider type.
//
// Ignoring them would let "type: vertex" sit above a base_url the operator
// believes is in effect.
func rejectFields(where, typ string, fields ...field) []error {
	var problems []error
	for _, f := range fields {
		if strings.TrimSpace(f.value) != "" {
			problems = append(problems,
				fmt.Errorf("%s: %s is not valid for type %s", where, f.name, typ))
		}
	}
	return problems
}
