package mockupstream

import (
	"encoding/json"
	"fmt"
	"hash/fnv"
	"math/rand/v2"
	"net/http"
	"strings"
	"unicode/utf8"
)

// lexicon is the fixed vocabulary the canned completions are drawn from. A
// literal list rather than generated text: the corpus has to be identical on
// every machine and every build for responses to be reproducible.
var lexicon = []string{
	"patient", "presents", "with", "elevated", "markers", "consistent",
	"findings", "indicate", "moderate", "response", "to", "therapy",
	"recommend", "follow-up", "imaging", "within", "six", "weeks",
	"laboratory", "values", "remain", "within", "expected", "range",
	"no", "acute", "distress", "noted", "on", "examination",
	"history", "suggests", "chronic", "but", "stable", "presentation",
	"further", "evaluation", "may", "clarify", "the", "diagnosis",
}

// contentRNG returns a generator for response TEXT.
//
// It is seeded separately from the chaos generator on purpose. If both drew
// from one stream, turning jitter on would shift the stream position and
// silently change the generated words — making a latency knob alter response
// bytes, which is exactly what determinism forbids. Salting the seed keeps
// content independent of every timing and failure setting.
func contentRNG(cfg Config, body []byte) *rand.Rand {
	h := fnv.New64a()
	_, _ = h.Write([]byte("content"))
	_, _ = h.Write(body)
	_, _ = h.Write([]byte(cfg.Model))
	_, _ = h.Write([]byte(cfg.Content))
	lo := h.Sum64() ^ cfg.Seed
	hi := lo*0xff51afd7ed558ccd + 0x9e3779b97f4a7c15
	return rand.New(rand.NewPCG(lo, hi))
}

// completionWords builds the canned reply as a word slice, so the streaming and
// buffered paths emit the SAME text and cannot drift apart. An explicit Content
// override is returned as its own fields.
func completionWords(cfg Config, body []byte) []string {
	if cfg.Content != "" {
		return strings.Fields(cfg.Content)
	}
	n := cfg.CompletionTokens
	if n <= 0 {
		return nil
	}
	rng := contentRNG(cfg, body)
	words := make([]string, n)
	for i := range words {
		words[i] = lexicon[rng.IntN(len(lexicon))]
	}
	// Capitalize the opening word and terminate the sentence so the output reads
	// like prose rather than a bag of tokens.
	if len(words) > 0 {
		words[0] = strings.ToUpper(words[0][:1]) + words[0][1:]
		words[len(words)-1] += "."
	}
	return words
}

// estimateTokens approximates a tokenizer at ~4 characters per token, the rule
// of thumb OpenAI publishes. Deterministic, and close enough that usage figures
// scale believably with prompt size.
func estimateTokens(s string) int {
	if s == "" {
		return 0
	}
	n := utf8.RuneCountInString(s) / 4
	if n < 1 {
		n = 1
	}
	return n
}

// responseID derives a stable, provider-shaped id from the request. A random or
// time-based id would be the single easiest way to break byte-identical runs.
func responseID(prefix string, cfg Config, body []byte) string {
	h := fnv.New64a()
	_, _ = h.Write(body)
	_, _ = h.Write([]byte(cfg.Model))
	return fmt.Sprintf("%s-%016x", prefix, h.Sum64()^cfg.Seed)
}

// --- OpenAI wire shapes -----------------------------------------------------
//
// These are structs rather than map[string]any so field ORDER in the encoded
// JSON is fixed by declaration order. Map encoding is also stable in Go (keys
// are sorted), but structs make the contract legible.

type oaUsage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
}

type oaMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type oaChoice struct {
	Index        int       `json:"index"`
	Message      oaMessage `json:"message"`
	Logprobs     *string   `json:"logprobs"`
	FinishReason string    `json:"finish_reason"`
}

type oaCompletion struct {
	ID                string     `json:"id"`
	Object            string     `json:"object"`
	Created           int64      `json:"created"`
	Model             string     `json:"model"`
	Choices           []oaChoice `json:"choices"`
	Usage             oaUsage    `json:"usage"`
	SystemFingerprint string     `json:"system_fingerprint"`
}

type oaDelta struct {
	Role    string `json:"role,omitempty"`
	Content string `json:"content,omitempty"`
}

type oaChunkChoice struct {
	Index        int     `json:"index"`
	Delta        oaDelta `json:"delta"`
	FinishReason *string `json:"finish_reason"`
}

type oaChunk struct {
	ID      string          `json:"id"`
	Object  string          `json:"object"`
	Created int64           `json:"created"`
	Model   string          `json:"model"`
	Choices []oaChunkChoice `json:"choices"`
	Usage   *oaUsage        `json:"usage,omitempty"`
}

// --- Gemini native wire shapes ---------------------------------------------

type gemPart struct {
	Text string `json:"text"`
}

type gemContent struct {
	Parts []gemPart `json:"parts"`
	Role  string    `json:"role"`
}

type gemCandidate struct {
	Content      gemContent `json:"content"`
	FinishReason string     `json:"finishReason,omitempty"`
	Index        int        `json:"index"`
}

type gemUsage struct {
	PromptTokenCount     int `json:"promptTokenCount"`
	CandidatesTokenCount int `json:"candidatesTokenCount"`
	TotalTokenCount      int `json:"totalTokenCount"`
}

type gemResponse struct {
	Candidates    []gemCandidate `json:"candidates"`
	UsageMetadata *gemUsage      `json:"usageMetadata,omitempty"`
	ModelVersion  string         `json:"modelVersion"`
	ResponseID    string         `json:"responseId"`
}

// --- error envelopes --------------------------------------------------------

// errorEnvelope renders a failure in the OpenAI error shape, which is what the
// proxy's callers expect on the /v1 surface.
func errorEnvelope(status int, reason string) []byte {
	type body struct {
		Message string `json:"message"`
		Type    string `json:"type"`
		Code    string `json:"code"`
	}
	payload := struct {
		Error body `json:"error"`
	}{Error: body{
		Message: fmt.Sprintf("mock upstream injected %s (%d)", reason, status),
		Type:    errorType(status),
		Code:    reason,
	}}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return []byte(`{"error":{"message":"mock failure"}}`)
	}
	return encoded
}

// geminiErrorEnvelope renders the same failure in Google's shape, so a client
// speaking the native API is not handed an OpenAI-looking error.
func geminiErrorEnvelope(status int, reason string) []byte {
	type body struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
		Status  string `json:"status"`
	}
	payload := struct {
		Error body `json:"error"`
	}{Error: body{
		Code:    status,
		Message: fmt.Sprintf("mock upstream injected %s (%d)", reason, status),
		Status:  geminiStatus(status),
	}}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return []byte(`{"error":{"message":"mock failure"}}`)
	}
	return encoded
}

func errorType(status int) string {
	switch status {
	case http.StatusTooManyRequests:
		return "rate_limit_error"
	case http.StatusBadRequest:
		return "invalid_request_error"
	default:
		return "server_error"
	}
}

func geminiStatus(status int) string {
	switch status {
	case http.StatusTooManyRequests:
		return "RESOURCE_EXHAUSTED"
	case http.StatusServiceUnavailable:
		return "UNAVAILABLE"
	case http.StatusGatewayTimeout:
		return "DEADLINE_EXCEEDED"
	case http.StatusBadRequest:
		return "INVALID_ARGUMENT"
	default:
		return "INTERNAL"
	}
}
