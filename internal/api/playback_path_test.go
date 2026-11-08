package api

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLocalPlaybackPathUsesPreparedZeroCopyLink(t *testing.T) {
	root := t.TempDir()
	sourceDir := filepath.Join(root, "archive")
	tempDir := filepath.Join(root, "app-data", "temp")
	if err := os.MkdirAll(sourceDir, 0o755); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(sourceDir, "recording.m4a")
	if err := os.WriteFile(source, []byte("audio"), 0o644); err != nil {
		t.Fatal(err)
	}
	prepared := playbackLinkPath(tempDir, source)
	if err := os.MkdirAll(filepath.Dir(prepared), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Link(source, prepared); err != nil {
		t.Fatal(err)
	}

	playback, err := localPlaybackPath(tempDir, source)
	if err != nil {
		t.Fatal(err)
	}
	sourceInfo, err := os.Stat(source)
	if err != nil {
		t.Fatal(err)
	}
	playbackInfo, err := os.Stat(playback)
	if err != nil {
		t.Fatal(err)
	}
	if !os.SameFile(sourceInfo, playbackInfo) {
		t.Fatal("playback path is not a hard link to the source")
	}

	again, err := localPlaybackPath(tempDir, source)
	if err != nil {
		t.Fatal(err)
	}
	if again != playback {
		t.Fatalf("cached playback path changed: %q != %q", again, playback)
	}
}

func TestLocalPlaybackPathFailsWhenExternalLinkIsMissing(t *testing.T) {
	root := t.TempDir()
	sourceDir := filepath.Join(root, "archive")
	if err := os.MkdirAll(sourceDir, 0o755); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(sourceDir, "recording.m4a")
	if err := os.WriteFile(source, []byte("audio"), 0o644); err != nil {
		t.Fatal(err)
	}

	if _, err := localPlaybackPath(filepath.Join(root, "app-data", "temp"), source); err == nil {
		t.Fatal("expected a missing prepared link to fail promptly")
	}
}

func TestLocalPlaybackPathKeepsFilesAlreadyInsideDataDir(t *testing.T) {
	root := t.TempDir()
	tempDir := filepath.Join(root, "data", "temp")
	if err := os.MkdirAll(tempDir, 0o755); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(root, "data", "recording.m4a")
	if err := os.WriteFile(source, []byte("audio"), 0o644); err != nil {
		t.Fatal(err)
	}

	playback, err := localPlaybackPath(tempDir, source)
	if err != nil {
		t.Fatal(err)
	}
	if playback != source {
		t.Fatalf("playback path = %q, want original %q", playback, source)
	}
}
