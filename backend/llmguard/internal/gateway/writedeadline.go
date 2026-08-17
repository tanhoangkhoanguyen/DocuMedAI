package gateway

// Bounding a write to a client that has stopped reading.
//
// The leak: TCP applies backpressure, so once a client stops consuming, the
// kernel send buffer fills and the next Write blocks — indefinitely. Nothing
// already in the gateway catches that. r.Context() fires when a client
// DISCONNECTS, and this client has not; it is silent while still holding the
// socket open. The inter-frame watchdog does not fire either, because the
// upstream is healthy and still delivering. The request goes on holding its
// admission slot, and 256 such clients close the gateway to everyone.
//
// A deadline rather than a watchdog, unlike the upstream side. There is no way
// to interrupt a blocked Write from another goroutine — but the socket enforces
// a deadline natively, and refreshing it per frame measures the right quantity:
// time since the last SUCCESSFUL write, not total stream duration.

import (
	"errors"
	"log/slog"
	"net/http"
	"time"
)

// writeDeadline refreshes a per-write deadline on the underlying connection.
//
// A struct rather than a bare call because SetWriteDeadline can be unsupported,
// and that has to be reported once per stream rather than once per frame — a
// stream emits thousands, and the same warning thousands of times is how a real
// signal gets buried.
type writeDeadline struct {
	rc     *http.ResponseController
	every  time.Duration
	log    *slog.Logger
	prov   string
	warned bool // single-goroutine: only the streaming loop touches this
}

// newWriteDeadline prepares a deadline setter for w.
//
// A non-positive `every` disables it, matching the convention the other
// streaming knobs use: 0 means an operator explicitly asked for no bound.
func newWriteDeadline(w http.ResponseWriter, every time.Duration, log *slog.Logger, prov string) *writeDeadline {
	if every <= 0 {
		return &writeDeadline{}
	}
	return &writeDeadline{rc: http.NewResponseController(w), every: every, log: log, prov: prov}
}

// arm pushes the deadline out to now+every. Called before each frame's write,
// because the write is the thing that blocks.
//
// An ErrNotSupported is logged once and then tolerated. Refusing to serve would
// be the stricter reading of "never stream what cannot be bounded", but it turns
// a missing secondary protection into a total outage of the feature — and it is
// reachable by something as ordinary as a middleware wrapping ResponseWriter
// without an Unwrap method. The stream stays bounded by the inter-frame watchdog
// and StreamAbsoluteMax; only the stalled-reader case degrades.
// TestWriteDeadlineSupportedOnRealServer keeps that from happening silently.
func (d *writeDeadline) arm() {
	if d.rc == nil {
		return
	}
	err := d.rc.SetWriteDeadline(time.Now().Add(d.every))
	if err == nil || d.warned {
		return
	}
	d.warned = true
	if errors.Is(err, http.ErrNotSupported) {
		d.log.Warn("write deadline unsupported; a stalled reader can hold its slot "+
			"until the inter-frame or absolute bound fires",
			"provider", d.prov)
		return
	}
	d.log.Warn("setting the write deadline failed", "provider", d.prov, "err", err.Error())
}

// There is deliberately no clear() to undo the deadline at the end of a stream.
//
// It looks necessary — the deadline is set on the CONNECTION, and under
// keep-alive the connection outlives the response, so a leftover deadline would
// seem to fire on whatever request reuses it. net/http already prevents that:
// its connection serve loop calls SetWriteDeadline(time.Time{}) after every
// handler returns, before the connection goes back to idle (server.go, right
// after finishRequest). A clear() here would be a second, redundant syscall
// papering over a guarantee the standard library already makes.
//
// Verified empirically rather than assumed: a stream followed by a slower
// request on the same pooled connection succeeds identically with and without
// an explicit clear.

// writeFrame writes one SSE frame and flushes it, reporting the first error.
//
// The errors were previously discarded (`_, _ = w.Write(...)`), which was
// harmless only while nothing could make a write fail. A write deadline makes
// them meaningful: a discarded error means a stalled reader is never noticed and
// the loop keeps writing into a socket that is not draining.
func writeFrame(w http.ResponseWriter, flusher http.Flusher, payload []byte) error {
	if _, err := w.Write([]byte("data: ")); err != nil {
		return err
	}
	if _, err := w.Write(payload); err != nil {
		return err
	}
	if _, err := w.Write([]byte("\n\n")); err != nil {
		return err
	}
	flusher.Flush()
	return nil
}
