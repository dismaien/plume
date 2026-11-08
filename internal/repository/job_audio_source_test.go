package repository

import (
	"context"
	"strings"
	"testing"

	"quill/internal/models"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
)

func TestFindAudioSourceByIDDoesNotLoadTranscriptPayload(t *testing.T) {
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&models.TranscriptionJob{}); err != nil {
		t.Fatal(err)
	}

	title := "Long recording"
	artifactDir := "/tmp/artifact"
	mergedPath := "/tmp/merged.m4a"
	transcript := strings.Repeat("large transcript payload ", 1000)
	job := &models.TranscriptionJob{
		ID:              "audio-source-test",
		Title:           &title,
		Status:          models.StatusCompleted,
		AudioPath:       "/tmp/source.m4a",
		ArtifactDir:     &artifactDir,
		Transcript:      &transcript,
		IsMultiTrack:    true,
		MergedAudioPath: &mergedPath,
	}
	if err := db.Create(job).Error; err != nil {
		t.Fatal(err)
	}

	got, err := NewJobRepository(db).FindAudioSourceByID(context.Background(), job.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got.AudioPath != job.AudioPath || got.MergedAudioPath == nil || *got.MergedAudioPath != mergedPath {
		t.Fatalf("audio fields not loaded correctly: %#v", got)
	}
	if got.Transcript != nil {
		t.Fatal("audio lookup unexpectedly loaded transcript payload")
	}
}
