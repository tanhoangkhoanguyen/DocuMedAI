package gateway

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"sync/atomic"
	"time"

	"documedai/llmguard/provider"
)

// maxUpstreamBody caps how much of a provider's response we will buffer.
//
// The buffered path reads the whole body into memory so a failed attempt can be
// discarded and replayed, which means a provider that streams an unbounded
// response — broken, misconfigured or hostile — can exhaust the gateway's
// memory. LLMGuard sits in the request path, so that is a denial-of-service
// vector rather than a hypothetical.
//
// 10 MiB is roughly two orders of magnitude above the largest plausible chat
// completion (a 128k-token reply is well under 1 MiB of JSON), so a legitimate
// response never approaches it.
const maxUpstreamBody = 10 << 20 // 10 MiB

// readUpstreamBody reads r with a hard ceiling, distinguishing "exactly at the
// cap" from "over the cap".
//
// A bare io.LimitReader would silently TRUNCATE: the caller would hand a cut-off
// body to TranslateResponse and get a confusing JSON syntax error instead of the
// real problem. Reading one byte past the limit makes an oversized response a
// clear, named failure — and because it is an error rather than a short read, the
// retry loop treats it as a failed attempt instead of caching nonsense.
func readUpstreamBody(r io.Reader) ([]byte, error) {
	body, err := io.ReadAll(io.LimitReader(r, maxUpstreamBody+1))
	if err != nil {
		return nil, err
	}
	if len(body) > maxUpstreamBody {
		return nil, fmt.Errorf("upstream response exceeds %d bytes", maxUpstreamBody)
	}
	return body, nil
}

// Proxy is the HTTP handler for /v1/chat/completions. It owns the
// provider-agnostic concerns — rate limit → dedup → circuit breaker → retry —
// and delegates every vendor-specific detail (URL, auth, wire format) to a
// provider.Provider.
type Proxy struct {
	cfg    Config
	client *http.Client // shared, keep-alive pooled
	// streamClient is the buffered client's twin for the streaming path, differing
	// ONLY in its Timeout. See newProxy for why the two cannot be one.
	streamClient *http.Client
	admitter     *admitter
	limiter  *RateLimiter
	deduper  *Deduper
	breakers *breakerGroup
	metrics  *Metrics
	log      *slog.Logger
}

// newProxy assembles the handler. The breaker sharer is passed in rather than
// built here, and may be nil: a single-replica deployment and the whole test
// suite run without one, and a nil sharer is a no-op rather than a special case.
func newProxy(
	cfg Config, limiter *RateLimiter, deduper *Deduper,
	sharer *BreakerSharer, m *Metrics, log *slog.Logger,
) *Proxy {
	// One shared transport so TCP/TLS connections upstream are reused across
	// requests instead of re-handshaking every call.
	transport := &http.Transport{
		MaxIdleConns:        cfg.MaxIdleConns,
		MaxIdleConnsPerHost: cfg.MaxIdleConns,
		IdleConnTimeout:     90 * time.Second,
		ForceAttemptHTTP2:   true,
	}
	return &Proxy{
		cfg:    cfg,
		client: &http.Client{Transport: transport, Timeout: cfg.UpstreamTimeout},

		// Two clients over ONE transport, because http.Client.Timeout is per-client
		// but the connection pool lives on the transport. Sharing the transport keeps
		// MaxIdleConns a single budget; giving each its own would silently double it.
		//
		// They must differ because Timeout is an ABSOLUTE deadline that covers the
		// body read, which means one value cannot serve both paths. For a buffered
		// call the body arrives in one piece and 120s is a correct ceiling. For a
		// stream the body IS the response, delivered over its whole lifetime, so the
		// same 120s truncates any healthy generation that runs longer — cutting the
		// stream mid-sentence and reporting it as an upstream failure.
		//
		// So the streaming client's ceiling is StreamAbsoluteMax (30m), a backstop
		// set beyond any real completion. What actually bounds a stream is
		// inactivity — the write and inter-frame deadlines in serveStreaming — which
		// is the quantity that distinguishes a stalled stream from a slow one. A 0
		// here disables the backstop and leaves only those.
		streamClient: &http.Client{Transport: transport, Timeout: cfg.StreamAbsoluteMax},

		admitter: newAdmitter(cfg.MaxInFlight, m),
		limiter:  limiter,
		deduper:  deduper,
		breakers: newBreakerGroup(cfg, m, sharer),
		metrics:  m,
		log:      log,
	}
}

// ServeHTTP decodes the OpenAI-shaped request, resolves its provider, and
// dispatches to the streaming or buffered path.
func (p *Proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	start := time.Now()

	// Every rejection below this point happens before provider.For runs, so
	// there is no adapter to name — see providerUnknown.
	if r.Method != http.MethodPost {
		p.writeError(w, providerUnknown, "unknown", start, http.StatusMethodNotAllowed,
			"method not allowed", "invalid_request_error")
		return
	}

	body, err := io.ReadAll(r.Body)
	if err != nil {
		p.writeError(w, providerUnknown, "unknown", start, http.StatusBadRequest,
			"failed to read request body", "invalid_request_error")
		return
	}
	_ = r.Body.Close()

	var req provider.ChatRequest
	if decErr := json.Unmarshal(body, &req); decErr != nil {
		p.writeError(w, providerUnknown, "unknown", start, http.StatusBadRequest,
			"invalid JSON body", "invalid_request_error")
		return
	}
	model := req.Model
	if model == "" {
		p.writeError(w, providerUnknown, "unknown", start, http.StatusBadRequest,
			"field 'model' is required", "invalid_request_error")
		return
	}
	// Required, with no default: one model can be served by several upstreams, so
	// picking one for the caller would silently send traffic somewhere they did
	// not choose. An explicit 400 makes the omission theirs to fix.
	if req.Provider == "" {
		p.writeError(w, providerUnknown, model, start, http.StatusBadRequest,
			"field 'provider' is required — it names which upstream serves this model",
			"invalid_request_error")
		return
	}
	if len(req.Messages) == 0 {
		p.writeError(w, providerUnknown, model, start, http.StatusBadRequest,
			"field 'messages' must not be empty", "invalid_request_error")
		return
	}

	prov, err := provider.For(provider.Route{Provider: req.Provider, Model: model})
	if err != nil {
		// The model is known but unroutable, so it is labelled while the provider
		// is not — resolution is exactly what failed.
		var unknown *provider.UnknownModelError
		if errors.As(err, &unknown) {
			// The enabled list is quoted back so a caller can correct the request
			// without reading the operator's config. This does disclose the model
			// inventory, which is acceptable for an internal gateway.
			p.writeError(w, providerUnknown, model, start, http.StatusBadRequest,
				fmt.Sprintf("model %q is not enabled on provider %q; enabled routes: %s",
					model, req.Provider, strings.Join(provider.EnabledRoutes(), ", ")),
				"invalid_request_error")
			return
		}
		// An allowed route whose provider never registered is a loader bug, not a
		// bad request: the caller can do nothing about it, so it is a 500.
		p.log.Error("provider resolution failed",
			"provider", req.Provider, "model", model, "err", err.Error())
		p.writeError(w, providerUnknown, model, start, http.StatusInternalServerError,
			"provider unavailable", "upstream_error")
		return
	}
	// Past this point every observation carries the RESOLVED adapter's name, not
	// cfg.Provider. Once routing is config-driven those differ, and labelling with
	// the configured default would silently attribute traffic to the wrong upstream.
	provName := prov.Name()

	// --- Admission control (concurrency ceiling) ---
	//
	// Ordered BEFORE the rate limiter, which is not the obvious placement. The
	// limiter can block for up to RateWaitMax waiting for a token, and a request
	// blocked there already holds a goroutine and this request's buffers — exactly
	// the resource the semaphore bounds. Admitting first therefore covers the wait
	// itself; the reverse order would leave an unbounded number of requests
	// queueing outside the ceiling that is supposed to contain them.
	//
	// It runs AFTER provider.For, so a shed request is attributed to the route it
	// was actually for rather than to providerUnknown — a capacity incident is
	// diagnosed by which traffic was refused.
	release, admitted := p.admitter.tryAcquire()
	if !admitted {
		p.metrics.shed.WithLabelValues(provName, model).Inc()
		// Retry-After turns a refusal into a usable instruction. Without it every
		// shed client retries on its own schedule and they re-arrive together —
		// the same thundering herd the buffered path already passes upstream
		// hints to avoid. One second because the queue we are shedding drains in
		// roughly one upstream call.
		w.Header().Set("Retry-After", "1")
		// 429, not 503: the upstream is fine and the request is well-formed — the
		// gateway is out of capacity and the caller should come back. 503 would
		// tell the client the provider is down and, for clients that fail over on
		// it, send traffic away from a healthy upstream.
		p.writeError(w, provName, model, start, http.StatusTooManyRequests,
			"gateway at capacity — too many concurrent requests", "rate_limit")
		return
	}
	// Deferred rather than released at each exit: the streaming path returns from
	// several places and can panic mid-stream, and a slot leaked once is leaked for
	// the process's lifetime — the ceiling would silently ratchet down to zero.
	defer release()

	// --- Rate limit (token bucket, per key+model) ---
	rlKey := apiKeyHint(r) + ":" + model
	if !p.limiter.Acquire(r.Context(), rlKey, p.cfg.RateWaitMax) {
		p.metrics.rateLimited.WithLabelValues(provName, model).Inc()
		p.writeError(w, provName, model, start, http.StatusTooManyRequests,
			"proxy rate limit exceeded", "rate_limit")
		return
	}

	// --- Cross-replica breaker check ---
	//
	// Another replica has recently found this provider down. Fail fast on its
	// evidence rather than collecting our own: at N replicas an upstream otherwise
	// absorbs N × CircuitMinReqs doomed requests before anything trips, and a
	// replica restarted mid-outage starts over from zero.
	//
	// Placed before dispatch so it covers the streaming path too — which has no
	// retry loop to protect it.
	//
	// 503, matching what a locally-open breaker produces: the provider is
	// unavailable, which is a different claim from the 429s above. The local
	// breaker remains the authority on recovery, so this never blocks a half-open
	// probe from running once the flag lapses.
	if p.breakers.openElsewhere(r.Context(), provName) {
		p.writeError(w, provName, model, start, http.StatusServiceUnavailable,
			"upstream unavailable (circuit open)", "upstream_error")
		return
	}

	// Streaming requests cannot be buffered/deduped/replayed as a unit — they
	// get breaker protection but no retry/dedup.
	if req.Stream {
		p.serveStreaming(w, r, prov, &req, start)
		return
	}
	p.serveBuffered(w, r, prov, &req, body, start)
}

// serveBuffered handles the normal (non-streaming) path: dedup → breaker → retry.
func (p *Proxy) serveBuffered(
	w http.ResponseWriter, r *http.Request,
	prov provider.Provider, req *provider.ChatRequest, rawBody []byte, start time.Time,
) {
	model := req.Model
	provName := prov.Name()
	key := dedupKey(rawBody)

	res, shared, err := p.deduper.Do(key, func() (*upstreamResult, error) {
		// The breaker wraps the WHOLE retry loop: a tripped breaker should stop
		// us before we even start retrying. It is this provider's breaker, so a
		// failing upstream does not shed traffic bound for a healthy one.
		v, berr := p.breakers.get(provName).Execute(func() (interface{}, error) {
			return doWithRetry(r.Context(), p.cfg, key,
				func() { p.metrics.retries.WithLabelValues(provName, model).Inc() },
				func(ctx context.Context) (*upstreamResult, error) {
					return p.forwardBuffered(ctx, prov, req)
				},
			)
		})
		if berr != nil {
			return nil, berr
		}
		return v.(*upstreamResult), nil
	})

	if err != nil {
		// A translated upstream error carries the vendor's own message and
		// status; anything else (breaker open, transport failure) is a 503.
		var ue *provider.UpstreamError
		if errors.As(err, &ue) {
			// Pass the provider's pacing hint through. Without it a client
			// facing a 429 has to guess when to come back, which is how a
			// thundering herd re-forms the moment quota frees up.
			if ue.RetryAfter != "" {
				w.Header().Set("Retry-After", ue.RetryAfter)
			}
			p.writeError(w, provName, model, start, ue.Status,
				ue.Body.Error.Message, ue.Body.Error.Type)
			return
		}
		p.log.Warn("upstream failed", "provider", provName, "model", model, "err", err.Error())
		p.writeError(w, provName, model, start, http.StatusServiceUnavailable,
			"upstream unavailable", "upstream_error")
		return
	}

	// Counted only once the flight has produced a real response. Every error
	// branch above returns, so reaching here means the shared result was
	// actually usable.
	//
	// A coalesced FAILURE is not a dedup hit. The counter answers "did a
	// thundering herd collapse into one upstream call" — a reliability signal —
	// and a flight that ended in a breaker-open 503 or a transport error
	// delivered nothing to share. Counting it would report the herd as absorbed
	// when in fact every caller failed.
	if shared {
		p.metrics.dedupHits.Inc()
	}

	p.recordUsage(provName, model, res.usage)
	p.writeJSON(w, provName, model, start, res.status, res.body, "buffered")
}

// forwardBuffered performs ONE upstream attempt: build → send → translate. The
// translated response is buffered so it can be retried and deduped.
func (p *Proxy) forwardBuffered(
	ctx context.Context, prov provider.Provider, req *provider.ChatRequest,
) (*upstreamResult, error) {
	httpReq, err := prov.BuildRequest(ctx, req)
	if err != nil {
		return nil, err
	}
	resp, err := p.client.Do(httpReq)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	nativeBody, err := readUpstreamBody(resp.Body)
	if err != nil {
		return nil, err
	}

	translated, err := prov.TranslateResponse(resp.StatusCode, nativeBody)
	if err != nil {
		// Surface the status so the retry loop can decide (429/5xx retryable).
		var ue *provider.UpstreamError
		if errors.As(err, &ue) {
			// TranslateResponse only sees (status, body), so the header has to
			// be attached here. The error is what survives the breaker and
			// deduper on the failure path; the result below is not.
			ue.RetryAfter = resp.Header.Get("Retry-After")
			return &upstreamResult{status: ue.Status, header: resp.Header.Clone()}, err
		}
		return nil, err
	}

	out, err := json.Marshal(translated)
	if err != nil {
		return nil, err
	}
	return &upstreamResult{
		status: http.StatusOK,
		header: resp.Header.Clone(),
		body:   out,
		usage:  translated.Usage,
	}, nil
}

// serveStreaming translates the provider's SSE stream into OpenAI chunks,
// flushing each as it arrives and terminating with `data: [DONE]`.
func (p *Proxy) serveStreaming(
	w http.ResponseWriter, r *http.Request,
	prov provider.Provider, req *provider.ChatRequest, start time.Time,
) {
	model := req.Model
	provName := prov.Name()
	flusher, ok := w.(http.Flusher)
	if !ok {
		p.writeError(w, provName, model, start, http.StatusInternalServerError,
			"streaming unsupported", "upstream_error")
		return
	}

	// A cancellable child of the request context, so an idle upstream can be cut
	// without waiting for the client to disconnect. Cancelling it aborts the
	// in-flight body read, which is the only way to interrupt a scanner.Scan()
	// that is blocked waiting for bytes that are not coming.
	//
	// The cancel is deferred rather than called at each exit: the loop below
	// returns from several places, and a context left uncancelled leaks its
	// goroutine and timer for the life of the process.
	streamCtx, cancelStream := context.WithCancel(r.Context())
	defer cancelStream()

	// abortReason records WHY the stream was cut, written by the idle watchdog and
	// read after the loop. It exists because cancellation is indistinguishable
	// from any other read error by the time the scanner reports it: without this,
	// a deadline abort and a genuine transport failure produce the same error and
	// the metric could not tell them apart.
	var abortReason atomic.Pointer[string]

	var usage provider.Usage
	// Whether WriteHeader has gone out. Past that point the status is locked in
	// and everything already flushed belongs to the client, so a failure can only
	// be APPENDED to the stream — never rewritten as an error envelope.
	var wroteHeader bool
	_, err := p.breakers.get(provName).Execute(func() (interface{}, error) {
		httpReq, berr := prov.BuildRequest(streamCtx, req)
		if berr != nil {
			return nil, berr
		}
		resp, berr := p.streamClient.Do(httpReq)
		if berr != nil {
			return nil, berr
		}
		defer resp.Body.Close()

		if resp.StatusCode < 200 || resp.StatusCode > 299 {
			// An oversized error body is not worth its own failure mode here —
			// the status alone already tells us the request failed — so a read
			// error degrades to an empty body and TranslateResponse still
			// produces an *UpstreamError carrying the status.
			nativeBody, _ := readUpstreamBody(resp.Body)
			// Reuse the adapter's error translation: a non-2xx status makes it
			// return an *UpstreamError carrying the vendor's message.
			_, terr := prov.TranslateResponse(resp.StatusCode, nativeBody)
			if terr != nil {
				return nil, terr
			}
			return nil, errors.New("upstream error")
		}

		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		w.WriteHeader(http.StatusOK)
		wroteHeader = true
		p.metrics.requests.WithLabelValues(provName, model, statusLabel(http.StatusOK)).Inc()

		// --- Inter-frame watchdog ---
		//
		// Bounds the gap between two frames FROM UPSTREAM. Nothing else measures
		// it: a provider that sends one frame and then goes quiet — without
		// erroring and without closing — holds this slot at its own pace, and to
		// any total-duration bound it is indistinguishable from a slow generation.
		// Inter-frame time is the quantity that separates the two.
		//
		// A timer RESET per frame rather than a deadline: reset is what makes a
		// long healthy stream survive. An absolute bound of the same size would cut
		// exactly the long generations the streaming client exists to protect.
		//
		// Armed only after the header is out, so it covers the streaming phase and
		// not connect/TLS, which streamClient's own timeout already bounds.
		idle := newIdleWatchdog(p.cfg.StreamIdleTimeout, func() {
			reason := abortUpstreamIdle
			abortReason.Store(&reason)
			// Cancels the read the scanner is blocked in, which is what actually
			// unwinds the handler and returns the admission slot.
			cancelStream()
		})
		defer idle.stop()

		// --- Stalled-reader deadline ---
		//
		// Bounds how long a write to the CLIENT may block, which is the sharpest
		// form of the same leak. A client that opens a stream and stops reading
		// fills the kernel send buffer, and Flush() then blocks indefinitely:
		// r.Context() does not fire, because the client is silent rather than gone,
		// and the watchdog above does not help, because the upstream is healthy and
		// still delivering. Nothing measures the write side.
		//
		// A DEADLINE rather than a watchdog, unlike the upstream side: a blocked
		// Write cannot be interrupted from another goroutine, but the socket
		// enforces its own deadline natively. Refreshed after each flushed frame,
		// so it measures time since the last successful write and a long healthy
		// stream never approaches it.
		//
		// Not cleared on exit: net/http resets the connection's write deadline
		// after every handler returns, so a leftover cannot reach the next request
		// on a keep-alive connection. See writedeadline.go.
		writeDeadline := newWriteDeadline(w, p.cfg.StreamWriteIdle, p.log, provName)

		// Scan the provider's SSE frames line by line. Vertex sends
		// `data: {...}` per frame; blank lines separate events.
		scanner := bufio.NewScanner(resp.Body)
		scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024) // a frame can be large
		for scanner.Scan() {
			// Reset on EVERY line, including the blank separators and frames that
			// fail to translate below. The watchdog asks whether the upstream is
			// still sending, not whether what it sends is useful — a provider
			// emitting keepalives is alive, and cutting it would be wrong.
			idle.reset()

			line := strings.TrimSpace(scanner.Text())
			if line == "" || !strings.HasPrefix(line, "data:") {
				continue
			}
			payload := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
			if payload == "[DONE]" {
				continue // we emit our own terminator
			}

			chunks, cerr := prov.TranslateStreamChunk(req, []byte(payload))
			if cerr != nil {
				p.log.Warn("stream chunk translate failed",
					"provider", provName, "model", model, "err", cerr.Error())
				continue // a malformed frame shouldn't kill the whole stream
			}
			for _, ch := range chunks {
				if ch.Usage != nil {
					usage = *ch.Usage
				}
				enc, merr := json.Marshal(ch)
				if merr != nil {
					continue
				}
				// Armed BEFORE the write, since the write is what blocks. Setting it
				// afterwards would leave each frame's own write unbounded — the
				// deadline would always be measuring the previous frame.
				writeDeadline.arm()
				if werr := writeFrame(w, flusher, enc); werr != nil {
					// Return either way: continuing would keep writing into a socket
					// that is not accepting, and returning is what unwinds the handler
					// and gives the slot back.
					//
					// But only a DEADLINE is counted. A client that closes the
					// connection mid-stream — someone hitting stop, closing a tab —
					// also fails this write, and that is ordinary traffic rather than a
					// stalled reader. Counting it would inflate the very metric that is
					// supposed to say "clients are wedging streams", which is the same
					// reason absolute_max is only inferred after the header is out.
					if errors.Is(werr, os.ErrDeadlineExceeded) {
						reason := abortWriteIdle
						abortReason.Store(&reason)
					}
					return nil, werr
				}
			}
		}
		// Checked BEFORE scanner.Err(), because a cancelled read does not reliably
		// surface as one: depending on where the cancellation lands, the body can
		// report a clean EOF instead, and the loop would fall through to [DONE] —
		// reporting a truncated stream as a complete one, which is the failure mode
		// this whole change exists to remove.
		if reason := abortReason.Load(); reason != nil {
			return nil, fmt.Errorf("stream aborted: %s", *reason)
		}
		if serr := scanner.Err(); serr != nil {
			return nil, serr
		}

		_, _ = w.Write([]byte("data: [DONE]\n\n"))
		flusher.Flush()
		return nil, nil
	})
	if err != nil {
		// Counted before the branch below, because a deadline abort is a distinct
		// fault from an upstream error and the branch only distinguishes "did
		// anything reach the client". Recorded here rather than in the watchdog so
		// it fires exactly once per aborted stream, on the path that ends it.
		if reason := streamAbortReason(abortReason.Load(), err, wroteHeader); reason != "" {
			p.metrics.streamAborts.WithLabelValues(provName, model, reason).Inc()
		}
		p.log.Warn("streaming upstream failed",
			"provider", provName, "model", model, "err", err.Error())

		if !wroteHeader {
			// Nothing has reached the client, so the buffered path's error
			// envelope is still available. Previously this fell straight through
			// and the client got HTTP 200 with zero bytes — indistinguishable
			// from a successful empty completion.
			//
			// The return matters: writeError routes through writeJSON, which
			// already records requests and latency. Falling through to the tail
			// would observe latency twice for one request.
			var ue *provider.UpstreamError
			if errors.As(err, &ue) {
				// Same reasoning as the buffered path: a 429 without a pacing
				// hint is how a thundering herd re-forms.
				if ue.RetryAfter != "" {
					w.Header().Set("Retry-After", ue.RetryAfter)
				}
				p.writeError(w, provName, model, start, ue.Status,
					ue.Body.Error.Message, ue.Body.Error.Type)
				return
			}
			p.writeError(w, provName, model, start, http.StatusServiceUnavailable,
				"upstream unavailable", "upstream_error")
			return
		}

		// The header is out and frames are on the wire. The status cannot be
		// changed and the text the client already has must not be discarded, so
		// the error goes out IN BAND, appended to what was delivered: the client
		// keeps its partial answer and still learns the stream ended badly
		// instead of seeing a truncation it cannot distinguish from a clean end.
		enc, merr := json.Marshal(provider.NewErrorEnvelope("upstream stream failed", "upstream_error"))
		if merr == nil {
			_, _ = w.Write([]byte("data: "))
			_, _ = w.Write(enc)
			_, _ = w.Write([]byte("\n\n"))
		}
		// [DONE] regardless, so a client's read loop terminates normally rather
		// than hanging on a stream that never says it is finished.
		_, _ = w.Write([]byte("data: [DONE]\n\n"))
		flusher.Flush()
	}

	// The streaming path now accounts tokens too — the old byte-pipe could not.
	p.recordUsage(provName, model, usage)
	p.metrics.latency.WithLabelValues(provName, model).Observe(time.Since(start).Seconds())
}

// streamAbortReason names which deadline ended a stream, or "" when the failure
// was not a deadline at all.
//
// Two sources, because the deadlines are enforced in two different places and
// only one of them can announce itself:
//
//   - The watchdogs set `explicit` before cancelling, so they are self-reporting
//     and always win. Checked first for that reason.
//   - StreamAbsoluteMax is http.Client.Timeout, enforced beneath us. It cannot
//     set anything; it just surfaces as a deadline error indistinguishable from
//     any other. Inferring it is the only way it gets a name — and it is the
//     reason that most needs one, because it fires only when the other two have
//     already failed to.
//
// wroteHeader gates the inference: before the header goes out the same deadline
// error means connect/TLS/first-byte was slow, which is an upstream problem
// rather than a stream that overran. Attributing that to the backstop would put
// ordinary upstream slowness into the counter that is supposed to mean "the
// streaming deadlines are broken".
func streamAbortReason(explicit *string, err error, wroteHeader bool) string {
	if explicit != nil {
		return *explicit
	}
	if !wroteHeader {
		return ""
	}
	// Both forms appear depending on where the deadline lands: the transport
	// reports os.ErrDeadlineExceeded on the socket, while a cancelled request
	// context reports context.DeadlineExceeded.
	if errors.Is(err, os.ErrDeadlineExceeded) || errors.Is(err, context.DeadlineExceeded) {
		return abortAbsoluteMax
	}
	return ""
}

// recordUsage adds normalized token counts to metrics.
func (p *Proxy) recordUsage(provName, model string, u provider.Usage) {
	if u.PromptTokens > 0 {
		p.metrics.tokensUsed.WithLabelValues(provName, model, "prompt").Add(float64(u.PromptTokens))
	}
	if u.CompletionTokens > 0 {
		p.metrics.tokensUsed.WithLabelValues(provName, model, "completion").Add(float64(u.CompletionTokens))
	}
}

// writeJSON writes a JSON body and records metrics/log.
func (p *Proxy) writeJSON(
	w http.ResponseWriter, provName, model string, start time.Time,
	status int, body []byte, kind string,
) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)

	p.metrics.requests.WithLabelValues(provName, model, statusLabel(status)).Inc()
	p.metrics.latency.WithLabelValues(provName, model).Observe(time.Since(start).Seconds())
	p.log.Info("request",
		"provider", provName,
		"model", model,
		"status", status,
		"latency_ms", time.Since(start).Milliseconds(),
		"kind", kind,
	)
}

// writeError emits the OpenAI-shaped error envelope so clients see one error
// format regardless of which provider (or LLMGuard itself) produced it.
//
// provName is providerUnknown on the early-rejection paths, which run before an
// adapter is resolved.
func (p *Proxy) writeError(
	w http.ResponseWriter, provName, model string, start time.Time, status int, msg, typ string,
) {
	body, err := json.Marshal(provider.NewErrorEnvelope(msg, typ))
	if err != nil {
		body = []byte(`{"error":{"message":"internal error","type":"upstream_error"}}`)
	}
	p.writeJSON(w, provName, model, start, status, body, "error")
}

// --- small helpers ---

// apiKeyHint derives a short, non-secret bucket label from the caller's key so
// rate-limit buckets are per-key without logging the key itself.
func apiKeyHint(r *http.Request) string {
	auth := r.Header.Get("Authorization")
	auth = strings.TrimPrefix(auth, "Bearer ")
	if len(auth) <= 8 {
		return "anon"
	}
	return auth[len(auth)-6:] // last 6 chars — stable, low-collision, not the secret
}

func statusLabel(status int) string {
	switch {
	case status >= 500:
		return "5xx"
	case status == 429:
		return "429"
	case status >= 400:
		return "4xx"
	default:
		return "2xx"
	}
}
