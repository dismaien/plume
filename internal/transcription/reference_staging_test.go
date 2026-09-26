package transcription

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestStageReferenceAudioUsesIndependentTemporaryFile(t *testing.T) {
	root := t.TempDir()
	sourcePath := filepath.Join(root, "recording.m4a")
	if err := os.WriteFile(sourcePath, []byte("audio"), 0644); err != nil {
		t.Fatal(err)
	}

	service := &UnifiedTranscriptionService{
		tempDirectory:    filepath.Join(root, "plume-data"),
		audioStorageMode: AudioStorageReference,
	}
	stagedPath, cleanup, err := service.stageReferenceAudio(context.Background(), sourcePath, "job/one")
	if err != nil {
		t.Fatal(err)
	}
	if stagedPath == sourcePath {
		t.Fatal("reference audio was not staged")
	}

	sourceInfo, err := os.Stat(sourcePath)
	if err != nil {
		t.Fatal(err)
	}
	stagedInfo, err := os.Stat(stagedPath)
	if err != nil {
		t.Fatal(err)
	}
	if os.SameFile(sourceInfo, stagedInfo) {
		t.Fatal("staging must not share an inode with preservation audio")
	}
	stagedData, err := os.ReadFile(stagedPath)
	if err != nil || string(stagedData) != "audio" {
		t.Fatalf("staged audio differs from source: %q, %v", stagedData, err)
	}

	cleanup()
	if _, err := os.Stat(stagedPath); !os.IsNotExist(err) {
		t.Fatalf("staged path still exists after cleanup: %v", err)
	}
	if _, err := os.Stat(sourcePath); err != nil {
		t.Fatalf("cleanup removed the source audio: %v", err)
	}
}

func TestStageReferenceAudioLeavesCopiedModeUntouched(t *testing.T) {
	root := t.TempDir()
	sourcePath := filepath.Join(root, "recording.m4a")
	if err := os.WriteFile(sourcePath, []byte("audio"), 0644); err != nil {
		t.Fatal(err)
	}

	service := &UnifiedTranscriptionService{
		tempDirectory:    filepath.Join(root, "plume-data"),
		audioStorageMode: AudioStorageCopy,
	}
	stagedPath, cleanup, err := service.stageReferenceAudio(context.Background(), sourcePath, "job-one")
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	if stagedPath != sourcePath {
		t.Fatalf("copy mode path = %q, want %q", stagedPath, sourcePath)
	}
}
