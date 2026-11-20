package adapters

import (
	"strings"
	"testing"

	"quill/internal/transcription/interfaces"
)

func TestSortformerArgsPreserveRawHeadsForIdentityReclustering(t *testing.T) {
	adapter := NewSortformerAdapter(t.TempDir())
	args, err := adapter.buildSortformerArgs(
		interfaces.AudioInput{FilePath: "/tmp/input.wav"},
		map[string]interface{}{
			"output_format": "json",
			"max_speakers":  2,
		},
		t.TempDir(),
	)
	if err != nil {
		t.Fatalf("buildSortformerArgs: %v", err)
	}

	joined := strings.Join(args, " ")
	if !strings.Contains(joined, "--raw-heads") {
		t.Fatalf("expected raw Sortformer heads, got %q", joined)
	}
	if !strings.Contains(joined, "--max-speakers 4") {
		t.Fatalf("expected all four activity heads before reclustering, got %q", joined)
	}
	if strings.Contains(joined, "--max-speakers 2") {
		t.Fatalf("speaker count must not be enforced by temporal head merging: %q", joined)
	}
}

func TestExplicitIntParameterDoesNotInventSpeakerCount(t *testing.T) {
	if _, ok := explicitIntParameter(map[string]interface{}{}, "max_speakers"); ok {
		t.Fatal("missing max_speakers must remain unset")
	}
	if value, ok := explicitIntParameter(map[string]interface{}{"max_speakers": 2}, "max_speakers"); !ok || value != 2 {
		t.Fatalf("explicit max_speakers = (%d, %t), want (2, true)", value, ok)
	}
}
