package gateway

// An inactivity timer: fire unless something keeps saying "still alive".
//
// This is the shape every streaming deadline in the gateway needs, and it is NOT
// what a timeout normally means. A timeout bounds total duration, which for a
// stream is the wrong quantity — a healthy generation and a hung one both run
// long, and only the gap between events separates them. What has to be bounded
// is time since the last sign of life, so the clock restarts on every one.

import (
	"sync"
	"time"
)

// idleWatchdog calls onIdle when reset has not been called for the configured
// duration.
//
// It wraps time.Timer rather than being used directly because Timer.Reset has a
// documented hazard: resetting a timer that has already fired does not un-fire
// it, so a naive reset loop can call onIdle after the caller believes the
// watchdog was refreshed. The `fired` flag closes that — once onIdle has run,
// reset is inert, so the callback happens at most once and a late reset cannot
// resurrect a stream that was already cut.
type idleWatchdog struct {
	mu    sync.Mutex
	timer *time.Timer
	every time.Duration
	fired bool
}

// newIdleWatchdog starts a watchdog that fires onIdle after `every` of silence.
//
// A non-positive `every` disables it entirely and returns a watchdog whose
// methods are no-ops, matching the convention the streaming config knobs use: 0
// means an operator explicitly asked for no bound, not a bound of zero.
//
// onIdle runs on the timer's own goroutine, so it must not block. Callers pass a
// context cancel, which does not.
func newIdleWatchdog(every time.Duration, onIdle func()) *idleWatchdog {
	w := &idleWatchdog{every: every}
	if every <= 0 {
		return w
	}
	w.timer = time.AfterFunc(every, func() {
		w.mu.Lock()
		// Re-checked under the lock: stop() may have won the race while this
		// callback was already scheduled, and firing then would cut a stream that
		// had in fact finished.
		if w.fired {
			w.mu.Unlock()
			return
		}
		w.fired = true
		w.mu.Unlock()
		onIdle()
	})
	return w
}

// reset restarts the countdown. Calling it after the watchdog has fired does
// nothing — the decision to abort is final.
func (w *idleWatchdog) reset() {
	if w.timer == nil {
		return
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.fired {
		return
	}
	w.timer.Reset(w.every)
}

// stop disarms the watchdog permanently. Safe to call more than once, and safe
// to call after it has fired, so callers can defer it unconditionally.
func (w *idleWatchdog) stop() {
	if w.timer == nil {
		return
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	// Marked fired rather than merely stopped: Stop() cannot retract a callback
	// that is already running, and this is what makes that callback a no-op.
	w.fired = true
	w.timer.Stop()
}
