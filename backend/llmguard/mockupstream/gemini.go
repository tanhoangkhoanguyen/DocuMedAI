package mockupstream

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// gemRequest is the subset of Gemini's generateContent body the mock reads.
type gemRequest struct {
	Contents []gemReqContent `json:"contents"`
	// SystemInstruction counts toward the prompt just like a system message
	// does on the OpenAI surface.
	SystemInstruction *gemReqContent `json:"systemInstruction"`
	GenerationConfig  *struct {
		MaxOutputTokens int `json:"maxOutputTokens"`
	} `json:"generationConfig"`
}

type gemReqContent struct {
	Role  string    `json:"role"`
	Parts []gemPart `json:"parts"`
}

func (c gemReqContent) text() string {
	var b strings.Builder
	for _, p := range c.Parts {
		b.WriteString(p.Text)
		b.WriteString(" ")
	}
	return strings.TrimSpace(b.String())
}

// parseGeminiPath splits Google's "{model}:{method}" final path segment.
//
// Real paths look like /v1beta/models/gemini-2.5-flash:streamGenerateContent.
// The method rides on the URL rather than the body, so routing has to parse it
// rather than pattern-match a fixed suffix.
func parseGeminiPath(path string) (model, method string, ok bool) {
	idx := strings.Index(path, "/models/")
	if idx < 0 {
		return "", "", false
	}
	tail := path[idx+len("/models/"):]
	if tail == "" {
		return "", "", false
	}
	colon := strings.LastIndex(tail, ":")
	if colon < 0 {
		return tail, "", false
	}
	return tail[:colon], tail[colon+1:], true
}

// handleGemini serves the native surface: :generateContent (buffered) and
// :streamGenerateContent (SSE when ?alt=sse, otherwise a streamed JSON array).
func (s *Server) handleGemini(w http.ResponseWriter, r *http.Request) {
	model, method, ok := parseGeminiPath(r.URL.Path)
	if !ok || method == "" {
		writeFailure(w, http.StatusNotFound, 0, "unknown_method", true)
		return
	}
	if r.Method != http.MethodPost {
		writeFailure(w, http.StatusMethodNotAllowed, 0, "method_not_allowed", true)
		return
	}

	body, err := io.ReadAll(r.Body)
	if err != nil {
		writeFailure(w, http.StatusBadRequest, 0, "unreadable_body", true)
		return
	}

	cfg := Resolve(s.cfg, r)
	if model == "" {
		model = cfg.Model
	}

	if s.outage.active() {
		writeFailure(w, cfg.ErrorStatus, cfg.RetryAfter, "outage", true)
		return
	}

	verdict := decide(cfg, r, body)
	if !verdict.sleep(r) {
		return
	}
	if verdict.fail {
		writeFailure(w, verdict.status, cfg.RetryAfter, "error_rate", true)
		return
	}

	var req gemRequest
	_ = json.Unmarshal(body, &req)

	var prompt strings.Builder
	if req.SystemInstruction != nil {
		prompt.WriteString(req.SystemInstruction.text())
		prompt.WriteString(" ")
	}
	for _, c := range req.Contents {
		prompt.WriteString(c.text())
		prompt.WriteString(" ")
	}
	promptTokens := estimateTokens(strings.TrimSpace(prompt.String()))

	words := completionWords(cfg, body)
	if req.GenerationConfig != nil && req.GenerationConfig.MaxOutputTokens > 0 &&
		len(words) > req.GenerationConfig.MaxOutputTokens {
		words = words[:req.GenerationConfig.MaxOutputTokens]
	}

	switch method {
	case "generateContent":
		s.geminiBuffered(w, cfg, model, body, words, promptTokens)
	case "streamGenerateContent":
		s.geminiStream(w, r, cfg, model, body, words, promptTokens)
	case "countTokens":
		writeJSON(w, http.StatusOK, map[string]int{"totalTokens": promptTokens})
	default:
		writeFailure(w, http.StatusNotFound, 0, "unknown_method", true)
	}
}

func (s *Server) geminiBuffered(
	w http.ResponseWriter, cfg Config, model string, body []byte, words []string, promptTokens int,
) {
	resp := gemResponse{
		Candidates: []gemCandidate{{
			Content:      gemContent{Parts: []gemPart{{Text: strings.Join(words, " ")}}, Role: "model"},
			FinishReason: "STOP",
			Index:        0,
		}},
		UsageMetadata: &gemUsage{
			PromptTokenCount:     promptTokens,
			CandidatesTokenCount: len(words),
			TotalTokenCount:      promptTokens + len(words),
		},
		ModelVersion: model,
		ResponseID:   responseID("mock", cfg, body),
	}
	writeJSON(w, http.StatusOK, resp)
}

// geminiStream emits incremental candidates.
//
// Google supports two framings on this endpoint and clients rely on both:
// ?alt=sse gives SSE events, while the default is a single JSON array streamed
// element by element. Both are implemented so a client is never forced into the
// wrong one.
func (s *Server) geminiStream(
	w http.ResponseWriter, r *http.Request, cfg Config, model string, body []byte, words []string, promptTokens int,
) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeFailure(w, http.StatusInternalServerError, 0, "streaming_unsupported", true)
		return
	}

	sse := r.URL.Query().Get("alt") == "sse"
	if sse {
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
	} else {
		w.Header().Set("Content-Type", "application/json")
	}
	w.Header().Set("X-Mock-Upstream", "1")
	w.WriteHeader(http.StatusOK)

	id := responseID("mock", cfg, body)
	if !sse {
		_, _ = io.WriteString(w, "[")
		flusher.Flush()
	}

	for i, word := range words {
		// Checked before the write, so StallAfter=N delivers exactly N chunks and
		// then goes silent — a consumer asserting on what it received can count.
		if stallNow(cfg, r, i) {
			return
		}
		text := word
		if i > 0 {
			text = " " + word
		}
		chunk := gemResponse{
			Candidates: []gemCandidate{{
				Content: gemContent{Parts: []gemPart{{Text: text}}, Role: "model"},
				Index:   0,
			}},
			ModelVersion: model,
			ResponseID:   id,
		}
		// Usage and finishReason ride on the FINAL chunk, as they do upstream.
		if i == len(words)-1 {
			chunk.Candidates[0].FinishReason = "STOP"
			chunk.UsageMetadata = &gemUsage{
				PromptTokenCount:     promptTokens,
				CandidatesTokenCount: len(words),
				TotalTokenCount:      promptTokens + len(words),
			}
		}

		encoded, err := json.Marshal(chunk)
		if err != nil {
			return
		}
		// Google terminates SSE events with CRLFCRLF on this endpoint; the JSON
		// array form is comma-separated instead.
		if sse {
			if _, werr := fmt.Fprintf(w, "data: %s\r\n\r\n", encoded); werr != nil {
				return
			}
		} else {
			prefix := ""
			if i > 0 {
				prefix = ","
			}
			if _, werr := fmt.Fprintf(w, "%s%s", prefix, encoded); werr != nil {
				return
			}
		}
		flusher.Flush()

		if cfg.ChunkDelay > 0 {
			select {
			case <-time.After(cfg.ChunkDelay):
			case <-r.Context().Done():
				return
			}
		}
	}

	if !sse {
		_, _ = io.WriteString(w, "]")
		flusher.Flush()
	}
}
