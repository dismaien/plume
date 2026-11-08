package api

import "testing"

func TestExtractTranscriptTextPreservesJSONEscapes(t *testing.T) {
	raw := `{"text":"I said \"hello\".\nThen I woke up with café in mind.","segments":[]}`

	got := extractTranscriptText(&raw)
	want := "I said \"hello\".\nThen I woke up with café in mind."
	if got != want {
		t.Fatalf("transcript text = %q, want %q", got, want)
	}
}

func TestExtractTranscriptTextFallsBackToPlainText(t *testing.T) {
	raw := "  plain accepted transcript  "
	if got := extractTranscriptText(&raw); got != "plain accepted transcript" {
		t.Fatalf("transcript text = %q", got)
	}
}
