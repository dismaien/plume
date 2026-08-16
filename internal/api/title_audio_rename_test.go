package api

import (
	"os"
	"path/filepath"
	"testing"

	"quill/internal/models"
)

func TestMaybeRenameAudioPathForGeneratedTitlePreservesReferenceSource(t *testing.T) {
	dir := t.TempDir()
	source := filepath.Join(dir, "20260816 094626-947C66BF.m4a")
	if err := os.WriteFile(source, []byte("audio"), 0600); err != nil {
		t.Fatal(err)
	}

	job := &models.TranscriptionJob{AudioPath: source}
	got, err := maybeRenameAudioPathForGeneratedTitle(job, "260816 09:46 A Better Title", "reference")
	if err != nil {
		t.Fatal(err)
	}
	if got != source {
		t.Fatalf("path = %q, want canonical source %q", got, source)
	}
	if _, err := os.Stat(source); err != nil {
		t.Fatalf("canonical source was renamed or removed: %v", err)
	}
}
