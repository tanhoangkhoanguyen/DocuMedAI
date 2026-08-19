package provider

import (
	"fmt"
	"net/http"
)

// UpstreamError is a non-2xx response from a provider. Core inspects Status to
// decide whether to retry, and passes Body through to the caller.
type UpstreamError struct {
	Status int
	Body   ErrorEnvelope

	// RetryAfter is the vendor's Retry-After header, verbatim, when it sent
	// one. It rides on the error because that is the only thing that survives
	// the breaker on the failure path — the *upstreamResult holding the
	// response headers is discarded there. Core forwards it to the client
	// so a caller can honor the provider's pacing instead of guessing.
	//
	// Empty when absent. Not parsed here: core needs the raw value to pass on,
	// and both RFC 9110 forms (delta-seconds and HTTP-date) are valid to echo.
	RetryAfter string
}

func (e *UpstreamError) Error() string {
	return fmt.Sprintf("upstream %d: %s", e.Status, e.Body.Error.Message)
}

// ErrorType maps an upstream HTTP status onto the `type` field of OpenAI's error
// envelope, so a client sees one vocabulary no matter which vendor failed.
//
// Exported and living here rather than in an adapter because it is a property of
// the normalized error shape, not of any one vendor: every adapter needs it to
// build an ErrorEnvelope, and each keeping its own copy would let two providers
// answer differently for the same status.
func ErrorType(status int) string {
	switch {
	case status == http.StatusTooManyRequests:
		return "rate_limit"
	case status == http.StatusUnauthorized || status == http.StatusForbidden:
		return "auth_error"
	case status >= 500:
		return "upstream_error"
	default:
		return "invalid_request_error"
	}
}
