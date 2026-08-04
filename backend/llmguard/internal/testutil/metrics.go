package testutil

// Prometheus readers, so tests can assert on the gateway's instrumentation —
// dedup hits, retries burned, breaker state — instead of only on HTTP output.

import (
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
)

// CounterValue reads the current value of a single (non-vector) counter or
// gauge, so tests can assert on the proxy's Prometheus instrumentation —
// dedup hits, retries burned, breaker state — instead of only on HTTP output.
func CounterValue(t *testing.T, c prometheus.Collector) float64 {
	t.Helper()
	return testutil.ToFloat64(c)
}

// LabeledCounterValue reads one labeled child out of a CounterVec, matching
// the label order declared when the vector was created.
func LabeledCounterValue(t *testing.T, vec *prometheus.CounterVec, labels ...string) float64 {
	t.Helper()
	counter, err := vec.GetMetricWithLabelValues(labels...)
	if err != nil {
		t.Fatalf("testutil: bad labels %v: %v", labels, err)
	}
	return testutil.ToFloat64(counter)
}
