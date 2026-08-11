package gateway

import (
	"context"
	"errors"
	"fmt"
	"os"

	"documedai/llmguard/provider"
)

// setupProviders builds one adapter per declared provider, registers it, and
// installs the model→provider routes from the allowlist.
//
// This is the ONLY place that knows a vendor's name. Adding a vendor means a case
// in buildAdapter plus its adapter — proxy.go and the client contract are
// untouched.
//
// The allowlist is the sole authority: a model absent from model_list is
// unroutable afterwards, because provider.For has no fallback.
func setupProviders(ctx context.Context, cfg Config, mc *ModelConfig) error {
	return setupProvidersWith(ctx, cfg, mc, nil)
}

// adapterFactory builds the adapter for one declared provider.
//
// A parameter so tests can substitute adapters without reaching a live upstream:
// constructing the Vertex adapter for real resolves Application Default
// Credentials, which CI does not have.
type adapterFactory func(ctx context.Context, cfg Config, pc ProviderConfig) (provider.Provider, error)

// setupProvidersWith is setupProviders with an injectable factory. A nil factory
// uses the real adapters.
func setupProvidersWith(
	ctx context.Context, cfg Config, mc *ModelConfig, factory adapterFactory,
) error {
	if mc == nil {
		return errors.New("setupProviders: no model config")
	}
	if factory == nil {
		factory = buildAdapter
	}

	// Errors accumulate: a config naming three broken upstreams should report all
	// three, not make the operator fix them one restart at a time.
	var problems []error
	for _, pc := range mc.Providers {
		p, err := factory(ctx, cfg, pc)
		if err != nil {
			problems = append(problems, fmt.Errorf("provider %q: %w", pc.Name, err))
			continue
		}
		provider.Register(p)
	}
	if err := errors.Join(problems...); err != nil {
		return err
	}

	// Installed after every adapter registered, so the allowlist can never name
	// one that failed to construct.
	provider.SetRoutes(mc.EnabledRoutes())
	return nil
}

// buildAdapter constructs the real adapter for one provider entry.
//
// The adapter is registered under the CONFIG's provider name, not the adapter
// type's own name, so two instances of one type can coexist — two openai-compat
// upstreams, say — and so metrics and the circuit breaker key on something the
// operator chose.
func buildAdapter(ctx context.Context, cfg Config, pc ProviderConfig) (provider.Provider, error) {
	switch pc.Type {
	case providerTypeVertex:
		// Vertex authenticates with ADC and takes no key. The project comes from
		// the env var the config NAMES, so the file itself carries no identifiers.
		project := os.Getenv(pc.ProjectEnv)
		if project == "" {
			project = cfg.VertexProject
		}
		return provider.NewVertex(ctx, project, pc.Location)

	case providerTypeOpenAICompat:
		// The secret is read here and handed straight to the adapter; it is never
		// stored on ProviderConfig, so nothing that logs or serializes the config
		// can leak it.
		return provider.NewOpenAICompat(pc.Name, pc.BaseURL, os.Getenv(pc.APIKeyEnv))

	default:
		return nil, fmt.Errorf("unknown type %q", pc.Type)
	}
}
