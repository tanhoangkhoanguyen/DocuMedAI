package gateway

// Characterization: the request-validation guards in front of the pipeline, and
// upstream error passthrough on the buffered path.
// Harness and thresholds live in harness_test.go.

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"documedai/llmguard/internal/testutil"
	"documedai/llmguard/mockupstream"
)

func TestRequestValidation(t *testing.T) {
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
			name:   "empty messages",
			method: http.MethodPost,
			// provider and model are both set, so messages is the only thing wrong.
			body:     `{"provider":"mock","model":"gemini-2.5-flash","messages":[]}`,
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
// WAS A QUIRK, NOW FIXED — and this suite is what found it. Previously EVERY
// non-2xx was retried the full RetryMax times, including client errors that
// isRetryable() classifies as not worth retrying. The early-exit read
// `err == nil && !isRetryable(res.status)`, but TranslateResponse returns an
// *UpstreamError for any non-2xx, so err was never nil on a failure and `&&`
// short-circuited before isRetryable() was ever evaluated. On the success path
// forwardBuffered hardcodes status 200, so isRetryable() only ever saw 200 —
// its verdict could not affect control flow at all. A 400 or 401 cost 4
// upstream calls and 3 backoff sleeps.
//
// The comment on that line ("or a non-retryable client error like 400/401")
// described pre-Vertex behavior, when forwardBuffered returned (result, nil)
// for every status; the Vertex migration silently invalidated it.
//
// doWithRetry now classifies the error instead of requiring err == nil, so a
// non-retryable status exits after ONE attempt. Retryable statuses (429/5xx)
// are unchanged. Every status code and error type below is exactly what the
// client saw before — only the upstream attempt COUNT changed.
func TestUpstreamErrorPassthrough(t *testing.T) {
	cases := []struct {
		name        string
		mockStatus  int
		wantCode    int
		wantType    string
		wantHits    int64
		wantRetries float64
	}{
		// Retryable: the full RetryMax budget is spent before giving up.
		{name: "429 is retried then surfaced", mockStatus: http.StatusTooManyRequests,
			wantCode: http.StatusTooManyRequests, wantType: "rate_limit",
			wantHits: 4, wantRetries: 3},
		{name: "500 is retried then surfaced", mockStatus: http.StatusInternalServerError,
			wantCode: http.StatusInternalServerError, wantType: "upstream_error",
			wantHits: 4, wantRetries: 3},
		{name: "503 is retried then surfaced", mockStatus: http.StatusServiceUnavailable,
			wantCode: http.StatusServiceUnavailable, wantType: "upstream_error",
			wantHits: 4, wantRetries: 3},
		// Not retryable: one attempt, no backoff. Retrying a client error would
		// fail identically every time.
		{name: "400 is not retried", mockStatus: http.StatusBadRequest,
			wantCode: http.StatusBadRequest, wantType: "invalid_request_error",
			wantHits: 1, wantRetries: 0},
		{name: "401 is not retried", mockStatus: http.StatusUnauthorized,
			wantCode: http.StatusUnauthorized, wantType: "auth_error",
			wantHits: 1, wantRetries: 0},
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
			// The retries metric is the second witness: it counts attempts
			// beyond the first, so 0 proves no backoff sleep was burned.
			if got := testutil.LabeledCounterValue(
				t,
				h.metrics.retries,
				modelLabels("gemini-2.5-flash")...,
			); got != tc.wantRetries {
				t.Errorf("retries metric = %v, want %v", got, tc.wantRetries)
			}
		})
	}
}

// --- oversized upstream response ---------------------------------------------

// The buffered path reads the whole provider response into memory so a failed
// attempt can be replayed, so an unbounded body is a memory-exhaustion vector.
// readUpstreamBody caps it at maxUpstreamBody.
//
// The cap must produce an ERROR, not a truncated body: a silently cut-off
// response would reach TranslateResponse and surface as a confusing JSON syntax
// error rather than the real problem. As an error it also stays a failed
// attempt, so nothing nonsensical is cached or returned to the caller.
func TestOversizedUpstreamResponseIsRejected(t *testing.T) {
	cases := []struct {
		name     string
		size     int
		wantCode int
	}{
		// Just under the cap still has to work — the guard must not clip
		// legitimate traffic.
		{name: "just under the cap is served", size: maxUpstreamBody - (1 << 16), wantCode: http.StatusOK},
		{name: "over the cap is rejected", size: maxUpstreamBody + (1 << 16), wantCode: http.StatusServiceUnavailable},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := realDefaults()
			cfg.RetryBaseDly = time.Millisecond
			cfg.RetryMaxDly = 5 * time.Millisecond

			// A valid Vertex response whose text part is padded to the target
			// size, so the only thing under test is total body length.
			h := newHarnessWithHandler(t, cfg, mockupstream.DefaultConfig(), nil,
				func(_ http.Handler, w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusOK)
					_, _ = w.Write([]byte(`{"candidates":[{"content":{"parts":[{"text":"`))
					_, _ = w.Write([]byte(strings.Repeat("a", tc.size)))
					_, _ = w.Write([]byte(`"}],"role":"model"},"finishReason":"STOP","index":0}],` +
						`"usageMetadata":{"promptTokenCount":1,"candidatesTokenCount":1,"totalTokenCount":2}}`))
				})

			rec := h.do(t, chatBody("gemini-2.5-flash", "big response", false), nil)

			if rec.Code != tc.wantCode {
				t.Fatalf("status = %d, want %d", rec.Code, tc.wantCode)
			}

			if tc.wantCode == http.StatusOK {
				// The oversized case is what this test is about; for the
				// under-cap case just prove the body survived intact.
				got := decodeChat(t, rec)
				if len(got.Choices) != 1 || got.Choices[0].Message.Content == "" {
					t.Errorf("a response under the cap must pass through intact, got %+v", got)
				}
				return
			}

			// Oversized is a transport-class failure: no vendor status to
			// report, so it surfaces as the generic 503 rather than a
			// pass-through of the upstream's own (here 200) status.
			env := decodeError(t, rec)
			if env.Error.Message != "upstream unavailable" {
				t.Errorf("error.message = %q, want %q", env.Error.Message, "upstream unavailable")
			}
		})
	}
}
