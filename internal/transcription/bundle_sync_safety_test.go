package transcription

import (
	"context"
	"os"
	"path/filepath"
	"quill/internal/models"
	"testing"
	"time"
)

func TestDuplicateBundleCannotReplaceAcceptedTranscriptOrNames(t *testing.T) {
	db := setupSyncTestDB(t)
	root := t.TempDir()
	accepted := filepath.Join(root, "zz-accepted")
	stale := filepath.Join(root, "aa-stale")
	for _, path := range []string{accepted, stale} {
		mustMkdir(t, path)
		mustWrite(t, filepath.Join(path, "transcript.json"), `{"text":"stale copy"}`)
		mustWriteMetadata(t, path, &BundleMetadata{ID: "duplicate", Title: "Stale", Status: "completed", CreatedAt: time.Now(), UpdatedAt: time.Now()})
	}
	old := time.Now().Add(-time.Hour)
	os.Chtimes(MetadataPath(accepted), old, old)
	text := `{"text":"accepted human correction"}`
	job := models.TranscriptionJob{ID: "duplicate", AudioPath: "source.wav", ArtifactDir: &accepted, Status: models.StatusCompleted, Transcript: &text}
	if err := db.Create(&job).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.SpeakerMapping{TranscriptionJobID: job.ID, OriginalSpeaker: "a", CustomName: "Leona", MatchSource: "manual"}).Error; err != nil {
		t.Fatal(err)
	}
	result, err := newSyncService(t, db, root, nil).SyncFromFilesystem(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if result.Conflicts != 1 || result.Updated != 0 {
		t.Fatalf("unexpected sync outcome: %+v", result)
	}
	var after models.TranscriptionJob
	db.First(&after, "id = ?", job.ID)
	if after.Transcript == nil || *after.Transcript != text || after.ArtifactDir == nil || *after.ArtifactDir != accepted {
		t.Fatal("stale duplicate replaced accepted recording")
	}
	var mapping models.SpeakerMapping
	if err := db.First(&mapping).Error; err != nil {
		t.Fatal(err)
	}
	if mapping.CustomName != "Leona" || mapping.MatchSource != "manual" {
		t.Fatal("stale duplicate replaced human speaker mapping")
	}
}
func TestBundleSyncDoesNotOverwriteProcessingOrCandidate(t *testing.T) {
	for _, candidate := range []bool{false, true} {
		t.Run(map[bool]string{false: "processing", true: "candidate"}[candidate], func(t *testing.T) {
			db := setupSyncTestDB(t)
			root := t.TempDir()
			path := filepath.Join(root, "recording")
			mustMkdir(t, path)
			mustWrite(t, filepath.Join(path, "transcript.json"), `{"text":"stale file"}`)
			mustWriteMetadata(t, path, &BundleMetadata{ID: "active", Title: "Stale", Status: "completed", CreatedAt: time.Now(), UpdatedAt: time.Now()})
			text := `{"text":"accepted text"}`
			job := models.TranscriptionJob{ID: "active", AudioPath: "source.wav", ArtifactDir: &path, Transcript: &text, Status: models.StatusProcessing}
			if candidate {
				job.Status = models.StatusCompleted
				id := uint(42)
				job.CandidateTranscriptRevisionID = &id
			}
			if err := db.Create(&job).Error; err != nil {
				t.Fatal(err)
			}
			db.Model(&job).Update("updated_at", time.Now().Add(-time.Hour))
			result, err := newSyncService(t, db, root, nil).SyncFromFilesystem(context.Background())
			if err != nil {
				t.Fatal(err)
			}
			var after models.TranscriptionJob
			db.First(&after, "id = ?", job.ID)
			if result.Updated != 0 || after.Status != job.Status || *after.Transcript != text {
				t.Fatal("filesystem replay overwrote protected result")
			}
		})
	}
}
func TestNewDuplicateIdentityIsNotImportedByDirectoryOrder(t *testing.T) {
	db := setupSyncTestDB(t)
	root := t.TempDir()
	for _, name := range []string{"a", "b"} {
		path := filepath.Join(root, name)
		mustMkdir(t, path)
		mustWrite(t, filepath.Join(path, "transcript.json"), `{"text":"copy"}`)
		mustWriteMetadata(t, path, &BundleMetadata{ID: "ambiguous", Status: "completed", CreatedAt: time.Now(), UpdatedAt: time.Now()})
	}
	result, err := newSyncService(t, db, root, nil).SyncFromFilesystem(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if result.Imported != 0 || result.Conflicts != 2 {
		t.Fatalf("picked arbitrary copy: %+v", result)
	}
}
