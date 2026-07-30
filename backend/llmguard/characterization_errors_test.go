package main

// Characterization: the request-validation guards in front of the pipeline, and
// upstream error passthrough on the buffered path.
// Harness and thresholds live in characterization_helpers_test.go.

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"documedai/llmguard/mockupstream"
)

func TestCharacterizeRequestValidation(t *testing.T) {
	cases := []struct {
		name     string
		method   string
		body     string
		wantCode int
		wantMsg  string
	}{
		{
			name:     "GET is rejected",
			method:   http.MethodGet,
			body:     "",
			wantCode: http.StatusMethodNotAllowed,
			wantMsg:  "method not allowed",
		},
		{
			name:     "malformed JSON",
			method:   http.MethodPost,
			body:     "{not json",
			wantCode: http.StatusBadRequest,
			wantMsg:  "invalid JSON body",
		},
		{
			name:     "missing model",
			method:   http.MethodPost,
			body:     `{"messages":[{"role":"user","content":"hi"}]}`,
			wantCode: http.StatusBadRequest,
			wantMsg:  "field 'model' is required",
		},
		{
			name:     "empty messages",
			method:   http.MethodPost,
			body:     `{"model":"gemini-2.5-flash","messages":[]}`,
			wantCode: http.StatusBadRequest,
			wantMsg:  "field 'messages' must not be empty",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := newHarness(t, realDefaults(), mockupstream.DefaultConfig(), nil)

			r := httptest.NewRequest(tc.method, "/v1/chat/completions", strings.NewReader(tc.body))
			rec := httptest.NewRecorder()
			h.proxy.ServeHTTP(rec, r)

			if rec.Code != tc.wantCode {
				t.Fatalf("status = %d, want %d\nbody: %s", rec.Code, tc.wantCode, rec.Body.String())
			}
			env := decodeError(t, rec)
			if env.Error.Message != tc.wantMsg {
				t.Errorf("error.message = %q, want %q", env.Error.Message, tc.wantMsg)
			}
			if env.Error.Type != "invalid_request_error" {
				t.Errorf("error.type = %q, want invalid_request_error", env.Error.Type)
			}
			// Nothing invalid should ever reach the provider.
			if h.up.Hits() != 0 {
				t.Errorf("upstream hits = %d, want 0 — validation runs before dispatch", h.up.Hits())
			}
		})
	}
}

// The buffered path surfaces the upstream's own status and message rather than
// flattening everything to 503.
//
// QUIRK (pinned, not fixed — this is a real defect, see the report): EVERY
// non-2xx is retried the full RetryMax times, including client errors that
// isRetryable() classifies as not worth retrying. The early-exit in retry.go:93
// is `err == nil && !isRetryable(res.status)`, but TranslateResponse returns an
// *UpstreamError for any non-2xx, so err is never nil on a failure and the
// isRetryable() check is unreachable. A 400 or 401 therefore costs 4 upstream
// calls. The comment on that line ("or a non-retryable client error like
// 400/401") describes the pre-Vertex behavior, when forwardBuffered returned
// (result, nil) for every status.
//
// Phase 2 will likely fix this. These expectations must then be updated
// deliberately — that is the signal, not a broken test.
func TestCharacterizeUpstreamErrorPassthrough(t *testing.T) {
	cases := []struct {
		name       string
		mockStatus int
		wantCode   int
		wantType   string
		wantHits   int64
	}{
		{name: "429 is retried then surfaced", mockStatus: http.StatusTooManyRequests,
			wantCode: http.StatusTooManyRequests, wantType: "rate_limit", wantHits: 4},
		{name: "500 is retried then surfaced", mockStatus: http.StatusInternalServerError,
			wantCode: http.StatusInternalServerError, wantType: "upstream_error", wantHits: 4},
		{name: "503 is retried then surfaced", mockStatus: http.StatusServiceUnavailable,
			wantCode: http.StatusServiceUnavailable, wantType: "upstream_error", wantHits: 4},
		// Retried despite being a client error — see the QUIRK above.
		{name: "400 is retried even though it is not retryable", mockStatus: http.StatusBadRequest,
			wantCode: http.StatusBadRequest, wantType: "invalid_request_error", wantHits: 4},
		{name: "401 is retried even though it is not retryable", mockStatus: http.StatusUnauthorized,
			wantCode: http.StatusUnauthorized, wantType: "auth_error", wantHits: 4},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := realDefaults()
			cfg.RetryBaseDly = time.Millisecond
			cfg.RetryMaxDly = 5 * time.Millisecond

			mcfg := mockupstream.DefaultConfig()
			mcfg.ErrorRate = 1.0
			mcfg.ErrorStatus = tc.mockStatus
			h := newHarness(t, cfg, mcfg, nil)

			rec := h.do(t, chatBody("gemini-2.5-flash", "fail please", false), nil)

			if rec.Code != tc.wantCode {
				t.Errorf("status = %d, want %d\nbody: %s", rec.Code, tc.wantCode, rec.Body.String())
			}
			if got := decodeError(t, rec).Error.Type; got != tc.wantType {
				t.Errorf("error.type = %q, want %q", got, tc.wantType)
			}
			if got := h.up.Hits(); got != tc.wantHits {
				t.Errorf("upstream hits = %d, want %d", got, tc.wantHits)
			}
		})
	}
}
