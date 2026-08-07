package provider

import (
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// update regenerates every golden fixture instead of asserting against it:
//
//	go test ./provider/ -update
//
// Review the resulting diff before committing — a fixture that changed because
// the translation changed is the signal these tests exist to produce.
var update = flag.Bool("update", false, "regenerate golden fixtures in testdata/")

// goldenDir is where fixtures live. .gitattributes pins it to LF.
const goldenDir = "testdata"

// normalizeNewlines collapses CRLF and lone CR to LF.
//
// The repo runs with core.autocrlf=true, so a Windows checkout can hand back
// CRLF even though the fixture is LF in git. Comparing normalized text makes the
// assertion independent of how the file was checked out; .gitattributes stops
// the CRLF from being committed in the first place. Neither defense alone is
// enough — the attributes file only governs fresh checkouts, and normalization
// alone would let a CRLF fixture get committed and confuse the next reader.
func normalizeNewlines(b []byte) string {
	s := strings.ReplaceAll(string(b), "\r\n", "\n")
	return strings.ReplaceAll(s, "\r", "\n")
}

// marshalGolden renders v as the indented JSON written to fixtures. Indented so
// a diff points at the field that changed rather than at one long line.
func marshalGolden(t *testing.T, v any) []byte {
	t.Helper()
	encoded, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		t.Fatalf("marshal golden: %v", err)
	}
	return append(encoded, '\n')
}

// assertGolden compares got against testdata/<name>, or rewrites it under
// -update.
func assertGolden(t *testing.T, name string, got []byte) {
	t.Helper()
	path := filepath.Join(goldenDir, name)

	if *update {
		if err := os.MkdirAll(goldenDir, 0o755); err != nil {
			t.Fatalf("create %s: %v", goldenDir, err)
		}
		if err := os.WriteFile(path, got, 0o644); err != nil {
			t.Fatalf("write %s: %v", path, err)
		}
		t.Logf("updated %s", path)
		return
	}

	want, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v\n(generate it with: go test ./provider/ -update)", path, err)
	}
	if normalizeNewlines(want) != normalizeNewlines(got) {
		t.Errorf("golden %s mismatch\n--- want ---\n%s\n--- got ---\n%s\n"+
			"(if the new output is correct: go test ./provider/ -update)",
			path, want, got)
	}
}

// assertGoldenJSON marshals v and compares it against the named fixture.
func assertGoldenJSON(t *testing.T, name string, v any) {
	t.Helper()
	assertGolden(t, name, marshalGolden(t, v))
}
