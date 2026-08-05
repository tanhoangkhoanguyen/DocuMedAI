package mockupstream

// Tests for the mock upstream's own contract.
//
// The mock is the foundation every resilience test and the whole Phase 6
// benchmark stands on, so the properties asserted here are the ones whose
// silent loss would invalidate work elsewhere rather than merely fail locally:
//
//   - Determinism. A benchmark baseline is only comparable across runs if the
//     same request yields the same bytes. A stray time.Now() or package-level
//     rand would not fail any existing test — it would show up much later as
//     unexplained variance in Phase 6 results.
//   - Content/timing independence. Latency and ChunkDelay must never reach
//     fingerprint(), or changing a timing knob would silently change response
//     bytes and failure verdicts, making two "identical" benchmark arms differ.
//   - The knobs AC 1.2 names: error_rate=1.0 always fails, Retry-After is
//     emitted, SSE terminates.
//   - The nonce escape hatch, without which a load generator repeating one body
//     gets all-fail or all-succeed instead of the configured rate.
//   - Outage covering BOTH surfaces, which retry_test.go
//     depends on via the Gemini path.
//
// Everything is driven through the exported API (New, DefaultConfig, Resolve,
// StartOutage, ServeHTTP) so these tests survive internal refactoring.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

const (
	oaPath   = "/v1/chat/completions"
	gemPath  = "/v1beta/models/gemini-2.5-flash:generateContent"
	chatBody = `{"model":"gemini-2.5-flash","messages":[{"role":"user","content":"summarize the discharge note"}]}`
	gemBody  = `{"contents":[{"role":"user","parts":[{"text":"summarize the discharge note"}]}]}`
)

// post drives one request through the server and returns the recorder.
func post(t *testing.T, s *Server, path, body string, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	r := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		r.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	s.ServeHTTP(rec, r)
	return rec
}

// --- determinism -------------------------------------------------------------

// The core guarantee: identical request + identical config → identical bytes,
// both on one instance and across a freshly constructed one (which stands in for
// a separate process or a later CI run).
func TestDeterminismByteIdenticalAcrossInstances(t *testing.T) {
	cases := []struct {
		name string
		path string
		body string
	}{
		{name: "openai buffered", path: oaPath, body: chatBody},
		{name: "openai stream", path: oaPath, body: `{"model":"m","stream":true,"messages":[{"role":"user","content":"hi"}]}`},
		{name: "gemini native", path: gemPath, body: gemBody},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := New(DefaultConfig())

			first := post(t, s, tc.path, tc.body, nil)
			if first.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200\nbody: %s", first.Code, first.Body.String())
			}

			// Repeat on the same instance.
			second := post(t, s, tc.path, tc.body, nil)
			if first.Body.String() != second.Body.String() {
				t.Errorf("same instance returned different bytes\nfirst:  %s\nsecond: %s",
					first.Body.String(), second.Body.String())
			}

			// Repeat on a new instance — catches state accumulated on the Server
			// as well as any clock or global RNG in the response path.
			fresh := post(t, New(DefaultConfig()), tc.path, tc.body, nil)
			if fresh.Body.String() != first.Body.String() {
				t.Errorf("a fresh instance returned different bytes — response is not a pure "+
					"function of the request\nfirst: %s\nfresh: %s",
					first.Body.String(), fresh.Body.String())
			}
		})
	}
}

// Byte-equality alone does not catch a coarse clock: time.Now().Unix() has
// one-second granularity, so two calls in the same second agree and the
// comparison above passes while determinism is already broken. Assert the
// timestamp IS the configured constant, which pins it regardless of granularity.
func TestDeterminismTimestampIsConfiguredNotClock(t *testing.T) {
	cfg := DefaultConfig()
	if cfg.Created != DefaultCreated {
		t.Fatalf("DefaultConfig().Created = %d, want the fixed DefaultCreated %d",
			cfg.Created, DefaultCreated)
	}

	rec := post(t, New(cfg), oaPath, chatBody, nil)
	var completion oaCompletion
	if err := json.Unmarshal(rec.Body.Bytes(), &completion); err != nil {
		t.Fatalf("response is not a completion: %v", err)
	}
	if completion.Created != DefaultCreated {
		t.Errorf("created = %d, want %d — a wall clock here breaks reproducibility even when "+
			"two calls in the same second happen to agree",
			completion.Created, DefaultCreated)
	}

	// An explicit override must be honored, proving the field is plumbed from
	// config rather than hardcoded (which would pass the check above trivially).
	custom := DefaultConfig()
	custom.Created = 1234567890
	rec = post(t, New(custom), oaPath, chatBody, nil)
	if err := json.Unmarshal(rec.Body.Bytes(), &completion); err != nil {
		t.Fatalf("response is not a completion: %v", err)
	}
	if completion.Created != 1234567890 {
		t.Errorf("created = %d, want the configured 1234567890", completion.Created)
	}
}

// Timing knobs must not change response BYTES. fingerprint() deliberately
// excludes Latency and ChunkDelay; if either were folded in, the per-request RNG
// seed would shift and both the generated text and the failure verdict would
// move with it — so two benchmark arms differing only in injected latency would
// stop being comparable.
func TestDeterminismContentIndependentOfTimingKnobs(t *testing.T) {
	baseline := post(t, New(DefaultConfig()), oaPath, chatBody, nil)
	if baseline.Code != http.StatusOK {
		t.Fatalf("baseline status = %d, want 200", baseline.Code)
	}

	slow := DefaultConfig()
	slow.Latency = 5 * time.Millisecond
	slow.Jitter = 5 * time.Millisecond
	slow.ChunkDelay = 5 * time.Millisecond

	got := post(t, New(slow), oaPath, chatBody, nil)
	if got.Body.String() != baseline.Body.String() {
		t.Errorf("latency/jitter/chunk_delay changed the response body — a timing knob must "+
			"never alter bytes\nno-delay: %s\nwith-delay: %s",
			baseline.Body.String(), got.Body.String())
	}

	// fingerprint() must exclude every timing knob, since it seeds the per-request
	// RNG that also decides the failure verdict.
	base := DefaultConfig().fingerprint()
	for name, cfg := range map[string]Config{
		"Latency":    {Latency: 5 * time.Millisecond},
		"Jitter":     {Jitter: 5 * time.Millisecond},
		"ChunkDelay": {ChunkDelay: 5 * time.Millisecond},
	} {
		merged := DefaultConfig()
		merged.Latency, merged.Jitter, merged.ChunkDelay = cfg.Latency, cfg.Jitter, cfg.ChunkDelay
		if got := merged.fingerprint(); got != base {
			t.Errorf("%s leaked into fingerprint():\n base: %s\n got:  %s", name, base, got)
		}
	}

	// Latency and ChunkDelay also leave the VERDICT untouched, which is the
	// property that makes two benchmark arms differing only in injected delay
	// comparable at the same error_rate.
	for _, tc := range []struct {
		name string
		cfg  func(Config) Config
	}{
		{"Latency", func(c Config) Config { c.Latency = 5 * time.Millisecond; return c }},
		{"ChunkDelay", func(c Config) Config { c.ChunkDelay = 5 * time.Millisecond; return c }},
	} {
		for i := range 40 {
			nonce := map[string]string{"X-Mock-Nonce": strings.Repeat("q", i+1)}
			plain := post(t, New(DefaultConfig()), oaPath+"?error_rate=0.5", chatBody, nonce)
			delayed := post(t, New(tc.cfg(DefaultConfig())), oaPath+"?error_rate=0.5", chatBody, nonce)
			if plain.Code != delayed.Code {
				t.Fatalf("%s, nonce %d: verdict differs (%d vs %d) — this knob must not reach "+
					"the failure decision", tc.name, i, plain.Code, delayed.Code)
			}
		}
	}
}

// QUIRK (pinned, not fixed): Jitter DOES change the failure verdict, even though
// it is correctly excluded from fingerprint().
//
// decide() draws in a fixed order — jitter, then the failure roll — but the
// jitter draw is CONDITIONAL on Jitter > 0 (chaos.go:76-78). Turning jitter on
// therefore consumes one number from the per-request stream and shifts the roll
// that follows, so the same request flips verdict at an unchanged error_rate.
// Measured: 21 of 40 nonces flip.
//
// The comment at chaos.go:74-75 claims the fixed draw order prevents exactly
// this; it holds only for knobs that always draw. A fix would draw jitter
// unconditionally and discard it when Jitter == 0, mirroring how the error-rate
// roll already always draws (chaos.go:84).
//
// Pinned rather than fixed, and now carrying a documented constraint instead of
// sitting as an unowned bug: determinism still holds WITHIN a config, so what a
// fix would buy is comparability ACROSS configs that differ only in jitter.
// Drawing jitter unconditionally changes every existing seeded value and
// invalidates any captured baseline, which makes it a Phase 6 decision about
// baselines rather than a Phase 1 bug fix.
//
// The rule that follows from it — HOLD Jitter FIXED ACROSS ARMS OF A COMPARISON,
// since two arms differing in jitter run against different failure sets — is
// stated where a reader will actually meet it: mockupstream/README.md
// ("Jitter shifts the failure verdict"), as a precondition on ROADMAP Issue 6.3,
// and in the ROADMAP Findings row.
func TestJitterShiftsFailureVerdictQuirk(t *testing.T) {
	jittered := DefaultConfig()
	jittered.Jitter = 5 * time.Millisecond

	var flips int
	const n = 40
	for i := range n {
		nonce := map[string]string{"X-Mock-Nonce": strings.Repeat("q", i+1)}
		plain := post(t, New(DefaultConfig()), oaPath+"?error_rate=0.5", chatBody, nonce)
		withJitter := post(t, New(jittered), oaPath+"?error_rate=0.5", chatBody, nonce)
		if plain.Code != withJitter.Code {
			flips++
		}
	}

	if flips == 0 {
		t.Errorf("jitter no longer shifts the failure verdict over %d nonces — if this was fixed "+
			"deliberately, delete this test and the Findings row; the fix is a real improvement", n)
	}
}

// --- injected failures -------------------------------------------------------

// AC 1.2: "Setting error-rate=1.0 makes it return 503 for every request."
// Asserted on both surfaces and over many requests, since a rate is only
// meaningful in aggregate.
func TestErrorRateOneAlwaysFails(t *testing.T) {
	cases := []struct {
		name    string
		path    string
		body    string
		wantErr string // JSON field proving the provider-shaped envelope was used
	}{
		{name: "openai", path: oaPath, body: chatBody, wantErr: "type"},
		{name: "gemini", path: gemPath, body: gemBody, wantErr: "status"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := New(DefaultConfig())
			for i := range 25 {
				rec := post(t, s, tc.path+"?error_rate=1.0", tc.body, nil)
				if rec.Code != http.StatusServiceUnavailable {
					t.Fatalf("request %d: status = %d, want 503", i, rec.Code)
				}
				if got := rec.Header().Get("X-Mock-Injected"); got != "error_rate" {
					t.Errorf("request %d: X-Mock-Injected = %q, want error_rate", i, got)
				}
			}

			// The error must arrive in the provider's own envelope shape, so a
			// client speaking that API is never handed a foreign-looking error.
			rec := post(t, s, tc.path+"?error_rate=1.0", tc.body, nil)
			var env map[string]map[string]any
			if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
				t.Fatalf("error body is not JSON: %v\nbody: %s", err, rec.Body.String())
			}
			if _, ok := env["error"][tc.wantErr]; !ok {
				t.Errorf("error envelope missing %q field; got %v", tc.wantErr, env["error"])
			}
		})
	}
}

// The nonce escape hatch. Identical bodies share one verdict by design, so a
// fractional error rate against a repeated body would return all-fail or
// all-succeed. X-Mock-Nonce and X-Request-Id opt a caller into a real
// distribution — without which no load generator can exercise a partial-failure
// scenario, which is Phase 6 scenario B.
func TestErrorRateVariesWithNonce(t *testing.T) {
	for _, header := range []string{"X-Mock-Nonce", "X-Request-Id"} {
		t.Run(header, func(t *testing.T) {
			s := New(DefaultConfig())

			var ok, failed int
			const n = 200
			for i := range n {
				rec := post(t, s, oaPath+"?error_rate=0.5", chatBody,
					map[string]string{header: string(rune('a'+i%26)) + strings.Repeat("z", i%11)})
				if rec.Code == http.StatusOK {
					ok++
				} else {
					failed++
				}
			}

			// Deliberately loose: this asserts a distribution exists, not that it
			// hits 50% precisely. A tight bound here would be a flaky test.
			if ok == 0 || failed == 0 {
				t.Errorf("%s produced no distribution at error_rate=0.5: ok=%d failed=%d — "+
					"the nonce escape hatch is broken", header, ok, failed)
			}
		})
	}
}

// Retry-After must be set BEFORE WriteHeader, or net/http drops it silently —
// the exact failure that would make the proxy's own Retry-After handling look
// broken when the mock is at fault.
//
// Asserted through Result().Header, NOT Header(). ResponseRecorder.Header()
// returns the live map, so a header written after WriteHeader is still visible
// there and the bug would pass unnoticed; Result() snapshots what was actually
// committed, matching what a real client receives.
func TestRetryAfterEmittedOnInjectedFailure(t *testing.T) {
	s := New(DefaultConfig())

	for _, path := range []string{oaPath, gemPath} {
		body := chatBody
		if path == gemPath {
			body = gemBody
		}
		rec := post(t, s, path+"?error_rate=1&error_status=429&retry_after=3", body, nil)

		if rec.Code != http.StatusTooManyRequests {
			t.Errorf("%s: status = %d, want 429", path, rec.Code)
		}
		if got := rec.Result().Header.Get("Retry-After"); got != "3" {
			t.Errorf("%s: Retry-After = %q, want \"3\" — a header set after WriteHeader is "+
				"dropped on a real connection", path, got)
		}
	}
}

// --- streaming ---------------------------------------------------------------

// A stream with no terminator hangs a real SSE client. This also pins that the
// concatenated deltas reproduce the buffered text exactly, which is what lets a
// test compare the two paths.
//
// Content is pinned via cfg.Content rather than left to the generator: the
// generated text is seeded from the request BODY, and a streaming request body
// necessarily differs from a buffered one ("stream":true), so the two would draw
// different words by design. Fixing the text isolates what is actually under
// test — that the delta framing reassembles losslessly.
func TestSSETerminatesAndReassembles(t *testing.T) {
	cfg := DefaultConfig()
	cfg.Content = "Patient presents with elevated markers consistent."
	s := New(cfg)

	rec := post(t, s, oaPath,
		`{"model":"gemini-2.5-flash","stream":true,"stream_options":{"include_usage":true},`+
			`"messages":[{"role":"user","content":"stream this"}]}`, nil)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if ct := rec.Header().Get("Content-Type"); ct != "text/event-stream" {
		t.Errorf("Content-Type = %q, want text/event-stream", ct)
	}

	body := rec.Body.String()
	if !strings.HasSuffix(strings.TrimSpace(body), "data: [DONE]") {
		t.Fatalf("stream must end with data: [DONE]; tail = %q",
			body[max(0, len(body)-40):])
	}

	var text strings.Builder
	var sawUsage, sawFinish bool
	for _, line := range strings.Split(body, "\n") {
		line = strings.TrimSpace(line)
		payload, ok := strings.CutPrefix(line, "data: ")
		if !ok || payload == "[DONE]" {
			continue
		}
		var chunk oaChunk
		if err := json.Unmarshal([]byte(payload), &chunk); err != nil {
			t.Fatalf("emitted a non-JSON chunk %q: %v", payload, err)
		}
		if chunk.Object != "chat.completion.chunk" {
			t.Errorf("object = %q, want chat.completion.chunk", chunk.Object)
		}
		for _, c := range chunk.Choices {
			text.WriteString(c.Delta.Content)
			if c.FinishReason != nil {
				sawFinish = true
			}
		}
		if chunk.Usage != nil {
			sawUsage = true
		}
	}

	if !sawFinish {
		t.Error("no chunk carried finish_reason")
	}
	if !sawUsage {
		t.Error("include_usage was requested but no chunk carried usage")
	}

	// The streamed deltas must reassemble to the same text the buffered path
	// returns, or a client that concatenates them sees something different from a
	// non-streaming caller — and no test could compare the two paths.
	buffered := post(t, s, oaPath,
		`{"model":"gemini-2.5-flash","messages":[{"role":"user","content":"stream this"}]}`, nil)
	var completion oaCompletion
	if err := json.Unmarshal(buffered.Body.Bytes(), &completion); err != nil {
		t.Fatalf("buffered response is not a completion: %v", err)
	}
	if got := text.String(); got != completion.Choices[0].Message.Content {
		t.Errorf("reassembled stream != buffered text\nstream:   %q\nbuffered: %q",
			got, completion.Choices[0].Message.Content)
	}
	if got := text.String(); got != cfg.Content {
		t.Errorf("reassembled stream = %q, want the configured content %q", got, cfg.Content)
	}
}

// --- outage ------------------------------------------------------------------

// The outage window is the one deliberately stateful, time-based knob, and it is
// the mechanism retry_test.go uses to make a retry observably
// differ from its predecessor. It must cover BOTH surfaces: the proxy's mock
// provider drives the Gemini path, so an OpenAI-only outage would leave that
// test silently exercising nothing.
func TestOutageAppliesToBothSurfacesThenClears(t *testing.T) {
	s := New(DefaultConfig())

	// Healthy before.
	if rec := post(t, s, oaPath, chatBody, nil); rec.Code != http.StatusOK {
		t.Fatalf("pre-outage status = %d, want 200", rec.Code)
	}

	s.StartOutage(time.Hour) // long enough that no timing race can end it mid-test

	for _, tc := range []struct{ name, path, body string }{
		{"openai", oaPath, chatBody},
		{"gemini", gemPath, gemBody},
	} {
		rec := post(t, s, tc.path, tc.body, nil)
		if rec.Code != http.StatusServiceUnavailable {
			t.Errorf("%s during outage: status = %d, want 503", tc.name, rec.Code)
		}
		if got := rec.Header().Get("X-Mock-Injected"); got != "outage" {
			t.Errorf("%s during outage: X-Mock-Injected = %q, want outage", tc.name, got)
		}
	}

	// An outage must override error_rate=0 — it is unconditional by definition.
	if rec := post(t, s, oaPath+"?error_rate=0", chatBody, nil); rec.Code != http.StatusServiceUnavailable {
		t.Errorf("outage with error_rate=0: status = %d, want 503 (outage is unconditional)", rec.Code)
	}

	// Clearing restores service; without this the knob would be a one-way door
	// and a test suite sharing one mock would poison every later case.
	s.StartOutage(0)
	if rec := post(t, s, oaPath, chatBody, nil); rec.Code != http.StatusOK {
		t.Errorf("post-outage status = %d, want 200 — a cleared outage must restore service", rec.Code)
	}
}

// --- config resolution -------------------------------------------------------
