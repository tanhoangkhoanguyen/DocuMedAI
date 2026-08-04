package mockupstream

import (
	"encoding/json"
	"net/http"
	"time"
)

// Server is the mock upstream. It implements http.Handler, so it can be run as
// a standalone process (cmd/mockupstream), mounted in a test's httptest.Server,
// or embedded in another mux — the response bytes are identical in all three.
type Server struct {
	cfg    Config
	mux    *http.ServeMux
	outage outage
}

// New builds a Server with its routes registered.
func New(cfg Config) *Server {
	s := &Server{cfg: cfg, mux: http.NewServeMux()}
	s.routes()
	return s
}

// Config returns the server's base configuration.
func (s *Server) Config() Config { return s.cfg }

// StartOutage opens a total-outage window of d, during which every request
// fails. A non-positive d clears it. Exposed so an in-process test can trigger
// an outage without an HTTP round trip.
func (s *Server) StartOutage(d time.Duration) time.Time { return s.outage.start(d) }

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.mux.ServeHTTP(w, r)
}

func (s *Server) routes() {
	// OpenAI-compatible surface. Registered on three prefixes because the proxy
	// forwards to whatever UpstreamBase names: /v1 for OpenAI itself, and
	// /v1beta/openai for Gemini's compatibility endpoint, which is what this
	// deployment actually points at.
	s.mux.HandleFunc("/v1/chat/completions", s.handleChatCompletions)
	s.mux.HandleFunc("/v1beta/openai/chat/completions", s.handleChatCompletions)
	s.mux.HandleFunc("/openai/chat/completions", s.handleChatCompletions)
	s.mux.HandleFunc("/v1/models", s.handleModels)
	s.mux.HandleFunc("/v1beta/openai/models", s.handleModels)

	// Gemini native surface. A prefix route, since the model name and method are
	// embedded in the path ("/models/{model}:{method}").
	s.mux.HandleFunc("/v1beta/models/", s.handleGemini)
	s.mux.HandleFunc("/v1/models/", s.handleGemini)

	// Operational endpoints, namespaced under /_mock so they can never collide
	// with a provider path.
	s.mux.HandleFunc("/healthz", s.handleHealth)
	s.mux.HandleFunc("/_mock/outage", s.handleOutage)
	s.mux.HandleFunc("/_mock/config", s.handleConfig)
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	// Reports ok even during an outage: this is the mock's own liveness, not the
	// simulated provider's. A container healthcheck must not flap because a test
	// deliberately triggered an outage.
	writeJSON(w, http.StatusOK, map[string]any{
		"status": "ok",
		"outage": s.outage.active(),
	})
}

// handleOutage opens or clears a total-outage window.
//
//	POST /_mock/outage?duration=10s   → all requests fail for 10s
//	POST /_mock/outage?duration=0     → clear
func (s *Server) handleOutage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeFailure(w, http.StatusMethodNotAllowed, 0, "method_not_allowed", false)
		return
	}
	d := pickDuration(r.URL.Query().Get("duration"), 0)
	until := s.outage.start(d)

	resp := map[string]any{"outage": d > 0, "duration": d.String()}
	if !until.IsZero() {
		resp["until"] = until.UTC().Format(time.RFC3339Nano)
	}
	writeJSON(w, http.StatusOK, resp)
}

// handleConfig reports the effective configuration for the request as sent,
// which makes a misapplied header or query parameter visible without having to
// infer it from response behavior.
func (s *Server) handleConfig(w http.ResponseWriter, r *http.Request) {
	cfg := Resolve(s.cfg, r)
	payload := map[string]any{
		"latency":           cfg.Latency.String(),
		"jitter":            cfg.Jitter.String(),
		"chunk_delay":       cfg.ChunkDelay.String(),
		"error_rate":        cfg.ErrorRate,
		"error_status":      cfg.ErrorStatus,
		"retry_after":       cfg.RetryAfter,
		"seed":              cfg.Seed,
		"model":             cfg.Model,
		"created":           cfg.Created,
		"completion_tokens": cfg.CompletionTokens,
		"outage_active":     s.outage.active(),
	}
	if until := s.outage.deadline(); !until.IsZero() {
		payload["outage_until"] = until.UTC().Format(time.RFC3339Nano)
	}
	writeJSON(w, http.StatusOK, payload)
}

// writeJSON encodes v and writes it with the given status.
func writeJSON(w http.ResponseWriter, status int, v any) {
	encoded, err := json.Marshal(v)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"error":{"message":"encode failed"}}`))
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(encoded)
}
