package testutil

// Prometheus readers, so tests can assert on the gateway's instrumentation —
// retries burned, requests shed, breaker state — instead of only on HTTP output.

import (
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
	dto "github.com/prometheus/client_model/go"
)

// CounterValue reads the current value of a single (non-vector) counter or
// gauge, so tests can assert on the proxy's Prometheus instrumentation —
// retries burned, requests shed, breaker state — instead of only on HTTP output.
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

// LabeledGaugeValue reads one labeled child out of a GaugeVec, matching the
// label order declared when the vector was created.
func LabeledGaugeValue(t *testing.T, vec *prometheus.GaugeVec, labels ...string) float64 {
	t.Helper()
	gauge, err := vec.GetMetricWithLabelValues(labels...)
	if err != nil {
		t.Fatalf("testutil: bad labels %v: %v", labels, err)
	}
	return testutil.ToFloat64(gauge)
}

// HistogramCount reads how many observations one labeled child of a
// HistogramVec has recorded — not their sum.
//
// It answers "how many times was this request counted", which is how a
// double-observed latency shows up. ToFloat64 cannot be used here: it panics on
// anything that is not a single-value metric, so the histogram has to be written
// out and read directly.
func HistogramCount(t *testing.T, vec *prometheus.HistogramVec, labels ...string) uint64 {
	t.Helper()
	observer, err := vec.GetMetricWithLabelValues(labels...)
	if err != nil {
		t.Fatalf("testutil: bad labels %v: %v", labels, err)
	}
	metric, ok := observer.(prometheus.Metric)
	if !ok {
		t.Fatalf("testutil: observer for %v is not a prometheus.Metric", labels)
	}
	var pb dto.Metric
	if werr := metric.Write(&pb); werr != nil {
		t.Fatalf("testutil: writing histogram %v: %v", labels, werr)
	}
	return pb.GetHistogram().GetSampleCount()
}
