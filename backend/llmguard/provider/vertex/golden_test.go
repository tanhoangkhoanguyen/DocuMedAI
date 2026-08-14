package vertex

// Golden fixtures for the Vertex adapter's wire bytes.
//
// The field assertions elsewhere in this package read a DECODED struct, so a
// change in what is emitted — a dropped key, a newly-omitted empty value, a
// reordered object — decodes back to the same struct and passes. These fixtures
// compare the actual bytes, which is the only way to catch that.
//
// There is deliberately NO -update flag. A golden you can regenerate turns "the
// bytes changed" into one command that re-blesses whatever the code now does,
// which is precisely the failure mode goldens exist to prevent. When a fixture
// must change, change it by hand and justify the diff in review.

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

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

// assertGolden compares got against testdata/<name>.
func assertGolden(t *testing.T, name string, got []byte) {
	t.Helper()
	path := filepath.Join(goldenDir, name)

	want, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v\n(fixtures are hand-authored; create it from the output below)", path, err)
	}
	if normalizeNewlines(want) != normalizeNewlines(got) {
		t.Errorf("golden %s mismatch\n--- want ---\n%s\n--- got ---\n%s\n"+
			"(edit the fixture by hand only if this change is intended)",
			path, want, got)
	}
}

// assertGoldenJSON marshals v and compares it against the named fixture.
func assertGoldenJSON(t *testing.T, name string, v any) {
	t.Helper()
	assertGolden(t, name, marshalGolden(t, v))
}
