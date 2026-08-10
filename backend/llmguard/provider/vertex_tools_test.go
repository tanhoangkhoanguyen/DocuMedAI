package provider

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"
)

// goldenCreated is the pinned `created` timestamp for fixtures. generateContent
// returns no timestamp, so the adapter reads a clock; pinning it is what keeps
// the response fixtures byte-stable. Same value as mockupstream.DefaultCreated
// (2025-01-01T00:00:00Z) so fixtures across the repo read alike.
const goldenCreated int64 = 1735689600

// goldenVertex is an adapter with a frozen clock and a counting id source, and
// no token source. It never reaches the network: these tests exercise translation
// only.
//
// Ids are sequential rather than random so fixtures stay byte-stable — the same
// reason the clock is frozen. The counter is per-adapter, so each test that calls
// goldenVertex() starts from 0 and fixtures do not depend on execution order.
func goldenVertex() *Vertex {
	var n int
	return &Vertex{
		project:  "test-project",
		location: "us-central1",
		now:      func() time.Time { return time.Unix(goldenCreated, 0).UTC() },
		newID: func() string {
			n++
			return fmt.Sprintf("%016x", n)
		},
	}
}

// realIDVertex is goldenVertex with the production id source, for the tests that
// are ABOUT id generation — uniqueness and prefix — where a pinned counter would
// assert the fake instead of the code.
func realIDVertex() *Vertex {
	return &Vertex{
		project:  "test-project",
		location: "us-central1",
		now:      func() time.Time { return time.Unix(goldenCreated, 0).UTC() },
	}
}

// --- golden: OpenAI request -> generateContent ---

func TestGoldenRequestText(t *testing.T) {
	temp := 0.2
	req := &ChatRequest{
		Model: "gemini-2.5-flash",
		Messages: []Message{
			{Role: "system", Content: "You are terse."},
			{Role: "user", Content: "What is the capital of France?"},
			{Role: "assistant", Content: "Paris."},
			{Role: "user", Content: "And of Japan?"},
		},
		Temperature: &temp,
	}
	assertGoldenJSON(t, "request/text.json", toNative(req))
}

// TestGoldenRequestTools is the tool-call golden for the request direction. It
// covers all three translations in one transcript: the tool catalogue, the
// model's call, and the result being handed back.
func TestGoldenRequestTools(t *testing.T) {
	req := &ChatRequest{
		Model: "gemini-2.5-flash",
		Messages: []Message{
			{Role: "system", Content: "Use tools when they help."},
			{Role: "user", Content: "Weather in Hanoi?"},
			{
				// Assistant turn that called a function instead of replying.
				Role: "assistant",
				ToolCalls: []ToolCall{{
					ID:   "call_0_get_weather",
					Type: "function",
					Function: FunctionCall{
						Name:      "get_weather",
						Arguments: `{"city":"Hanoi","unit":"celsius"}`,
					},
				}},
			},
			{
				// The result coming back, keyed by call id.
				Role:       "tool",
				ToolCallID: "call_0_get_weather",
				Content:    `{"temp_c":31,"conditions":"humid"}`,
			},
		},
		Tools: []Tool{{
			Type: "function",
			Function: FunctionDef{
				Name:        "get_weather",
				Description: "Current weather for a city.",
				Parameters: json.RawMessage(
					`{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}`),
			},
		}},
		ToolChoice: json.RawMessage(`"auto"`),
	}
	assertGoldenJSON(t, "request/tools.json", toNative(req))
}

// --- golden: generateContent -> OpenAI ---

// nativeTextResponse is a documented-shape generateContent reply.
const nativeTextResponse = `{
  "candidates": [{
    "content": {"role": "model", "parts": [{"text": "Tokyo."}]},
    "finishReason": "STOP",
    "index": 0
  }],
  "usageMetadata": {
    "promptTokenCount": 12,
    "candidatesTokenCount": 3,
    "thoughtsTokenCount": 5,
    "totalTokenCount": 20
  },
  "modelVersion": "gemini-2.5-flash-002"
}`

func TestGoldenResponseText(t *testing.T) {
	out, err := goldenVertex().TranslateResponse(http.StatusOK, []byte(nativeTextResponse))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}
	assertGoldenJSON(t, "response/text.json", out)
}

// nativeToolCallResponse is generateContent returning a functionCall. Note
// finishReason STOP: Gemini does not have OpenAI's "tool_calls" reason.
const nativeToolCallResponse = `{
  "candidates": [{
    "content": {
      "role": "model",
      "parts": [{"functionCall": {"name": "get_weather", "args": {"city": "Hanoi", "unit": "celsius"}}}]
    },
    "finishReason": "STOP",
    "index": 0
  }],
  "usageMetadata": {
    "promptTokenCount": 40,
    "candidatesTokenCount": 9,
    "totalTokenCount": 49
  },
  "modelVersion": "gemini-2.5-flash-002"
}`

// TestGoldenResponseToolCall is the tool-call golden for the response direction.
func TestGoldenResponseToolCall(t *testing.T) {
	out, err := goldenVertex().TranslateResponse(http.StatusOK, []byte(nativeToolCallResponse))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}
	assertGoldenJSON(t, "response/tool_call.json", out)
}

// --- golden: native SSE -> OpenAI chunks ---

// translateStream runs a sequence of native SSE payloads through the adapter and
// returns every chunk produced, in order.
//
// It feeds the adapter exactly what proxy.go feeds it — one `data:` payload,
// already unwrapped — so the fixture records the adapter's real output boundary.
// The `data: ` framing and the trailing `[DONE]` are written by proxy.go
// (proxy.go:320-331) and asserted by proxy_streaming_test.go; reconstructing
// them here would pin a copy of the proxy rather than the adapter.
func translateStream(t *testing.T, v *Vertex, req *ChatRequest, frames []string) []StreamChunk {
	t.Helper()
	var out []StreamChunk
	for _, f := range frames {
		chunks, err := v.TranslateStreamChunk(req, []byte(f))
		if err != nil {
			t.Fatalf("TranslateStreamChunk(%s): %v", f, err)
		}
		out = append(out, chunks...)
	}
	return out
}

func TestGoldenStreamText(t *testing.T) {
	frames := []string{
		`{"candidates":[{"content":{"role":"model","parts":[{"text":"Tok"}]},"index":0}],"modelVersion":"gemini-2.5-flash-002"}`,
		`{"candidates":[{"content":{"role":"model","parts":[{"text":"yo"}]},"index":0}],"modelVersion":"gemini-2.5-flash-002"}`,
		`{"candidates":[{"content":{"role":"model","parts":[{"text":"."}]},"finishReason":"STOP","index":0}],` +
			`"usageMetadata":{"promptTokenCount":12,"candidatesTokenCount":3,"totalTokenCount":15},` +
			`"modelVersion":"gemini-2.5-flash-002"}`,
	}

	chunks := translateStream(t, goldenVertex(), &ChatRequest{Model: "gemini-2.5-flash"}, frames)
	assertGoldenJSON(t, "stream/text.json", chunks)
}

func TestGoldenStreamToolCall(t *testing.T) {
	frames := []string{
		`{"candidates":[{"content":{"role":"model","parts":[{"functionCall":{"name":"get_weather","args":{"city":"Hanoi"}}}]},` +
			`"finishReason":"STOP","index":0}],` +
			`"usageMetadata":{"promptTokenCount":40,"candidatesTokenCount":9,"totalTokenCount":49},` +
			`"modelVersion":"gemini-2.5-flash-002"}`,
	}

	chunks := translateStream(t, goldenVertex(), &ChatRequest{Model: "gemini-2.5-flash"}, frames)
	assertGoldenJSON(t, "stream/tool_call.json", chunks)
}

// TestStreamTerminationIsCallerOwned documents where [DONE] comes from. The
// adapter yields nothing for a metadata-only trailing frame; the terminator is
// the proxy's to write.
func TestStreamTerminationIsCallerOwned(t *testing.T) {
	chunks, err := goldenVertex().TranslateStreamChunk(
		&ChatRequest{Model: "gemini-2.5-flash"},
		[]byte(`{"modelVersion":"gemini-2.5-flash-002"}`))
	if err != nil {
		t.Fatalf("TranslateStreamChunk: %v", err)
	}
	if len(chunks) != 0 {
		t.Errorf("chunks = %d, want 0 for a metadata-only frame", len(chunks))
	}
}

// --- tool translation: request direction ---

func TestToNativeToolDeclarations(t *testing.T) {
	params := json.RawMessage(`{"type":"object","properties":{"city":{"type":"string"}}}`)
	native := toNative(&ChatRequest{
		Model:    "gemini-2.5-flash",
		Messages: []Message{{Role: "user", Content: "hi"}},
		Tools: []Tool{
			{Type: "function", Function: FunctionDef{Name: "a", Description: "does a", Parameters: params}},
			{Type: "function", Function: FunctionDef{Name: "b"}},
		},
	})

	// Gemini nests every declaration under ONE tool entry, unlike OpenAI's
	// one-entry-per-function array.
	if len(native.Tools) != 1 {
		t.Fatalf("tools = %d, want 1", len(native.Tools))
	}
	decls := native.Tools[0].FunctionDeclarations
	if len(decls) != 2 {
		t.Fatalf("functionDeclarations = %d, want 2", len(decls))
	}
	if decls[0].Name != "a" || decls[0].Description != "does a" {
		t.Errorf("decls[0] = %+v", decls[0])
	}
	if string(decls[0].Parameters) != string(params) {
		t.Errorf("parameters mutated:\n got %s\nwant %s", decls[0].Parameters, params)
	}
	if decls[1].Name != "b" || decls[1].Parameters != nil {
		t.Errorf("decls[1] = %+v", decls[1])
	}
}

func TestToNativeAssistantToolCall(t *testing.T) {
	native := toNative(&ChatRequest{
		Messages: []Message{{
			Role: "assistant",
			ToolCalls: []ToolCall{{
				ID:       "call_0_get_weather",
				Type:     "function",
				Function: FunctionCall{Name: "get_weather", Arguments: `{"city":"Hanoi"}`},
			}},
		}},
	})

	if len(native.Contents) != 1 {
		t.Fatalf("contents = %d, want 1", len(native.Contents))
	}
	c := native.Contents[0]
	if c.Role != "model" {
		t.Errorf("role = %q, want model", c.Role)
	}
	// No empty text part alongside the call: vertexPart is a oneof.
	if len(c.Parts) != 1 {
		t.Fatalf("parts = %d, want 1 (no empty text part)", len(c.Parts))
	}
	if c.Parts[0].FunctionCall == nil {
		t.Fatal("functionCall part missing")
	}
	if c.Parts[0].FunctionCall.Name != "get_weather" {
		t.Errorf("name = %q", c.Parts[0].FunctionCall.Name)
	}
	// Byte-preserving: key order survives because args is never decoded.
	if got := string(c.Parts[0].FunctionCall.Args); got != `{"city":"Hanoi"}` {
		t.Errorf("args = %s, want the caller's bytes verbatim", got)
	}
}

func TestToNativeKeepsTextAlongsideToolCall(t *testing.T) {
	native := toNative(&ChatRequest{
		Messages: []Message{{
			Role:    "assistant",
			Content: "Let me check.",
			ToolCalls: []ToolCall{{
				ID:       "call_0_f",
				Function: FunctionCall{Name: "f", Arguments: `{}`},
			}},
		}},
	})
	parts := native.Contents[0].Parts
	if len(parts) != 2 {
		t.Fatalf("parts = %d, want 2 (text + functionCall)", len(parts))
	}
	if parts[0].Text != "Let me check." {
		t.Errorf("parts[0].text = %q", parts[0].Text)
	}
	if parts[1].FunctionCall == nil {
		t.Error("parts[1] should be the functionCall")
	}
}

func TestToNativeToolResult(t *testing.T) {
	native := toNative(&ChatRequest{
		Messages: []Message{
			{
				Role: "assistant",
				ToolCalls: []ToolCall{{
					ID:       "call_abc123",           // an id we did NOT mint
					Function: FunctionCall{Name: "get_weather", Arguments: `{}`},
				}},
			},
			{Role: "tool", ToolCallID: "call_abc123", Content: `{"temp_c":31}`},
		},
	})

	if len(native.Contents) != 2 {
		t.Fatalf("contents = %d, want 2", len(native.Contents))
	}
	result := native.Contents[1]
	// Gemini has no "tool" role; results ride on a user content.
	if result.Role != "user" {
		t.Errorf("role = %q, want user", result.Role)
	}
	fr := result.Parts[0].FunctionResponse
	if fr == nil {
		t.Fatal("functionResponse part missing")
	}
	// Name was recovered from the assistant turn, not parsed out of the id —
	// which is what makes a real OpenAI transcript translate correctly.
	if fr.Name != "get_weather" {
		t.Errorf("name = %q, want get_weather (looked up by call id)", fr.Name)
	}
	if string(fr.Response) != `{"temp_c":31}` {
		t.Errorf("response = %s", fr.Response)
	}
}

func TestToolResultPayloadWrapsNonObject(t *testing.T) {
	tests := []struct {
		name    string
		content string
		want    string
	}{
		{name: "object passes through", content: `{"a":1}`, want: `{"a":1}`},
		{name: "bare string is wrapped", content: "sunny", want: `{"result":"sunny"}`},
		{name: "array is wrapped", content: `[1,2]`, want: `{"result":"[1,2]"}`},
		{name: "empty is wrapped", content: "", want: `{"result":""}`},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := string(toolResultPayload(tc.content)); got != tc.want {
				t.Errorf("toolResultPayload(%q) = %s, want %s", tc.content, got, tc.want)
			}
		})
	}
}

func TestArgsObject(t *testing.T) {
	tests := []struct {
		name string
		args string
		want string
	}{
		{name: "object preserved verbatim", args: `{"b":1,"a":2}`, want: `{"b":1,"a":2}`},
		{name: "empty becomes empty object", args: "", want: "{}"},
		{name: "whitespace becomes empty object", args: "   ", want: "{}"},
		{
			// Corruption is carried through, not silently turned into a no-arg
			// call: `{}` would invoke the function with no parameters and look
			// identical to a legitimate one.
			name: "malformed is carried under _raw",
			args: `{"a":`,
			want: `{"_raw":"{\"a\":"}`,
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := string(argsObject(tc.args)); got != tc.want {
				t.Errorf("argsObject(%q) = %s, want %s", tc.args, got, tc.want)
			}
		})
	}
}

func TestToolChoiceConfig(t *testing.T) {
	tests := []struct {
		name          string
		raw           string
		wantMode      string
		wantAllowed   []string
		wantNilConfig bool
	}{
		{name: "unset", raw: "", wantNilConfig: true},
		{name: "none", raw: `"none"`, wantMode: "NONE"},
		{name: "auto", raw: `"auto"`, wantMode: "AUTO"},
		{name: "required", raw: `"required"`, wantMode: "ANY"},
		{
			name:        "pinned function",
			raw:         `{"type":"function","function":{"name":"get_weather"}}`,
			wantMode:    "ANY",
			wantAllowed: []string{"get_weather"},
		},
		{name: "unrecognized string", raw: `"whatever"`, wantNilConfig: true},
		{name: "object without a name", raw: `{"type":"function"}`, wantNilConfig: true},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			var raw json.RawMessage
			if tc.raw != "" {
				raw = json.RawMessage(tc.raw)
			}
			got := toolChoiceConfig(raw)

			if tc.wantNilConfig {
				if got != nil {
					t.Fatalf("toolConfig = %+v, want nil", got)
				}
				return
			}
			if got == nil || got.FunctionCallingConfig == nil {
				t.Fatalf("toolConfig = %+v, want a config", got)
			}
			if got.FunctionCallingConfig.Mode != tc.wantMode {
				t.Errorf("mode = %q, want %q", got.FunctionCallingConfig.Mode, tc.wantMode)
			}
			if strings.Join(got.FunctionCallingConfig.AllowedFunctionNames, ",") !=
				strings.Join(tc.wantAllowed, ",") {
				t.Errorf("allowedFunctionNames = %v, want %v",
					got.FunctionCallingConfig.AllowedFunctionNames, tc.wantAllowed)
			}
		})
	}
}

// --- tool translation: response direction ---

func TestTranslateResponseToolCalls(t *testing.T) {
	out, err := goldenVertex().TranslateResponse(http.StatusOK, []byte(nativeToolCallResponse))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}

	choice := out.Choices[0]
	// Gemini said STOP; OpenAI's contract says tool_calls.
	if choice.FinishReason != "tool_calls" {
		t.Errorf("finish_reason = %q, want tool_calls", choice.FinishReason)
	}
	if len(choice.Message.ToolCalls) != 1 {
		t.Fatalf("tool_calls = %d, want 1", len(choice.Message.ToolCalls))
	}
	tc := choice.Message.ToolCalls[0]
	if !strings.HasPrefix(tc.ID, "call-") {
		t.Errorf("id = %q, want a call- prefix", tc.ID)
	}
	if tc.Type != "function" {
		t.Errorf("type = %q", tc.Type)
	}
	// args object -> arguments string, bytes preserved.
	if tc.Function.Arguments != `{"city": "Hanoi", "unit": "celsius"}` {
		t.Errorf("arguments = %s", tc.Function.Arguments)
	}
}

// TestToolCallIDsAreUnique is why the id is random rather than derived from the
// call's content.
//
// Two calls to the SAME function with the SAME arguments in one turn are legal —
// the same search issued against two vendors, say. toNative keys results by id
// (toolNamesByCallID), so ids that collide would attach one result to the wrong
// call and drop the other. Nothing else in the payload distinguishes these two.
func TestToolCallIDsAreUnique(t *testing.T) {
	native := `{"candidates":[{"content":{"parts":[
		{"functionCall":{"name":"f","args":{"x":1}}},
		{"functionCall":{"name":"f","args":{"x":1}}}
	]},"finishReason":"STOP","index":0}]}`

	out, err := realIDVertex().TranslateResponse(http.StatusOK, []byte(native))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}
	calls := out.Choices[0].Message.ToolCalls
	if len(calls) != 2 {
		t.Fatalf("tool_calls = %d, want 2", len(calls))
	}
	if calls[0].ID == calls[1].ID {
		t.Fatalf("ids collide: %q — identical calls must still be addressable", calls[0].ID)
	}
	for i, c := range calls {
		if !strings.HasPrefix(c.ID, "call-") {
			t.Errorf("calls[%d].ID = %q, want a call- prefix", i, c.ID)
		}
	}
}

func TestToolCallArgsDefaultToEmptyObject(t *testing.T) {
	native := `{"candidates":[{"content":{"parts":[{"functionCall":{"name":"ping"}}]},"index":0}]}`
	out, err := goldenVertex().TranslateResponse(http.StatusOK, []byte(native))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}
	// "" is not valid JSON, and clients parse this field.
	if got := out.Choices[0].Message.ToolCalls[0].Function.Arguments; got != "{}" {
		t.Errorf("arguments = %q, want {}", got)
	}
}

func TestFinishReasonToolCallsDoesNotMaskTruncation(t *testing.T) {
	native := `{"candidates":[{"content":{"parts":[{"functionCall":{"name":"f","args":{}}}]},` +
		`"finishReason":"MAX_TOKENS","index":0}]}`
	out, err := goldenVertex().TranslateResponse(http.StatusOK, []byte(native))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}
	if got := out.Choices[0].FinishReason; got != "length" {
		t.Errorf("finish_reason = %q, want length — truncation must not be reported as tool_calls", got)
	}
}

func TestTranslateResponsePopulatesIDAndCreated(t *testing.T) {
	v := realIDVertex()
	out, err := v.TranslateResponse(http.StatusOK, []byte(nativeTextResponse))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}
	if !strings.HasPrefix(out.ID, "chatcmpl-") {
		t.Errorf("id = %q, want a chatcmpl- id", out.ID)
	}
	if out.Created != goldenCreated {
		t.Errorf("created = %d, want %d", out.Created, goldenCreated)
	}

	// Unique per response: two translations of the SAME body must differ, or a
	// client tracing by id cannot tell two calls apart.
	again, err := v.TranslateResponse(http.StatusOK, []byte(nativeTextResponse))
	if err != nil {
		t.Fatalf("TranslateResponse: %v", err)
	}
	if again.ID == out.ID {
		t.Errorf("id repeated across responses: %q", out.ID)
	}
}

// TestTimestampToleratesNilClock guards the zero-value construction that
// internal/gateway's test harness relies on.
func TestTimestampToleratesNilClock(t *testing.T) {
	out, err := (&Vertex{}).TranslateResponse(http.StatusOK, []byte(nativeTextResponse))
	if err != nil {
		t.Fatalf("TranslateResponse on a zero-value Vertex: %v", err)
	}
	if out.Created <= 0 {
		t.Errorf("created = %d, want a real timestamp", out.Created)
	}
}

// --- tool translation: streaming ---

func TestTranslateStreamChunkToolCall(t *testing.T) {
	frame := `{"candidates":[{"content":{"parts":[{"functionCall":{"name":"get_weather","args":{"city":"Hanoi"}}}]},` +
		`"finishReason":"STOP","index":0}],"modelVersion":"gemini-2.5-flash-002"}`

	chunks, err := goldenVertex().TranslateStreamChunk(&ChatRequest{Model: "gemini-2.5-flash"}, []byte(frame))
	if err != nil {
		t.Fatalf("TranslateStreamChunk: %v", err)
	}
	if len(chunks) != 1 {
		t.Fatalf("chunks = %d, want 1", len(chunks))
	}

	delta := chunks[0].Choices[0].Delta
	if len(delta.ToolCalls) != 1 {
		t.Fatalf("delta.tool_calls = %d, want 1", len(delta.ToolCalls))
	}
	d := delta.ToolCalls[0]
	if d.Index != 0 {
		t.Errorf("index = %d, want 0", d.Index)
	}
	if !strings.HasPrefix(d.ID, "call-") || d.Type != "function" {
		t.Errorf("id/type = %q/%q, want a call- prefix and function", d.ID, d.Type)
	}
	if d.Function == nil {
		t.Fatal("function fragment missing")
	}
	// Gemini does not fragment: the whole call arrives in one delta. Arguments
	// are the frame's bytes verbatim — note the frame has no space after the
	// colon, and neither does this.
	if d.Function.Name != "get_weather" || d.Function.Arguments != `{"city":"Hanoi"}` {
		t.Errorf("function = %+v", *d.Function)
	}
	if chunks[0].Choices[0].FinishReason != "tool_calls" {
		t.Errorf("finish_reason = %q, want tool_calls", chunks[0].Choices[0].FinishReason)
	}
}

// --- regression: text-only behavior is unchanged ---

// TestToNativeEmptyContentStillEmitsPart pins that a plain message with empty
// content keeps its (empty) text part, so adding the oneof did not silently
// change the text-only path.
func TestToNativeEmptyContentStillEmitsPart(t *testing.T) {
	native := toNative(&ChatRequest{Messages: []Message{{Role: "user", Content: ""}}})
	if len(native.Contents) != 1 {
		t.Fatalf("contents = %d, want 1", len(native.Contents))
	}
	if len(native.Contents[0].Parts) != 1 {
		t.Fatalf("parts = %d, want 1", len(native.Contents[0].Parts))
	}
	if native.Contents[0].Parts[0].Text != "" {
		t.Errorf("text = %q, want empty", native.Contents[0].Parts[0].Text)
	}
}

func TestToNativeWithoutToolsOmitsToolFields(t *testing.T) {
	native := toNative(&ChatRequest{Messages: []Message{{Role: "user", Content: "hi"}}})
	if native.Tools != nil {
		t.Errorf("tools = %+v, want nil", native.Tools)
	}
	if native.ToolConfig != nil {
		t.Errorf("toolConfig = %+v, want nil", native.ToolConfig)
	}

	// And they must not appear in the encoded body at all.
	encoded, err := json.Marshal(native)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	for _, key := range []string{"tools", "toolConfig", "functionCall", "functionResponse"} {
		if strings.Contains(string(encoded), key) {
			t.Errorf("body contains %q for a tool-free request: %s", key, encoded)
		}
	}
}
