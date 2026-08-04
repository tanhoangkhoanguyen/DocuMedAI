package gateway

import (
	"context"
	"fmt"

	"documedai/llmguard/provider"
)

// setupProviders constructs and registers every adapter this build supports,
// then wires the model→provider routing rules.
//
// This is the ONLY place main knows a vendor's name. Adding a provider means
// adding a case here plus its adapter — proxy.go and the client contract are
// untouched.
func setupProviders(ctx context.Context, cfg Config) error {
	switch cfg.Provider {
	case "vertex":
		v, err := provider.NewVertex(ctx, cfg.VertexProject, cfg.VertexLocation)
		if err != nil {
			return err
		}
		provider.Register(v)
		provider.RouteModel("gemini-", v.Name())
		return nil
	default:
		return fmt.Errorf("unknown provider %q (set LLMGUARD_PROVIDER)", cfg.Provider)
	}
}
