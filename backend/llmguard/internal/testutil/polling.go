package testutil

// Polling helpers for state that settles asynchronously — the circuit breaker
// reopening after its timeout, a metric incremented on a background goroutine.
// A bare sleep is either flaky or slow; these wait only as long as needed.

import (
	"testing"
	"time"
)

// Eventually polls cond until it returns true or timeout elapses.
//
// The proxy is full of state that settles asynchronously — the circuit breaker
// reopening after its timeout, a metric incremented on a background goroutine —
// and a bare sleep is either flaky or slow. Returns true if cond ever held.
func Eventually(timeout, interval time.Duration, cond func() bool) bool {
	if interval <= 0 {
		interval = 10 * time.Millisecond
	}
	deadline := time.Now().Add(timeout)
	for {
		if cond() {
			return true
		}
		if time.Now().After(deadline) {
			return false
		}
		time.Sleep(interval)
	}
}

// RequireEventually is Eventually with a failure attached, for the common case
// where the condition not holding means the test failed.
func RequireEventually(t *testing.T, timeout, interval time.Duration, cond func() bool, msg string) {
	t.Helper()
	if !Eventually(timeout, interval, cond) {
		t.Fatalf("testutil: condition never held within %s: %s", timeout, msg)
	}
}
