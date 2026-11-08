package database

import (
	"os"
	"path/filepath"
	"testing"

	"quill/internal/models"
)

func TestFolderMigrationOnlyTouchesOwnedLegacyPaths(t *testing.T) {
	db := newBackfillTestDB(t)
	root := t.TempDir()
	v := models.Vault{Name: "test", Path: root}
	if err := db.Create(&v).Error; err != nil {
		t.Fatal(err)
	}
	old := "Transcripts/old-title-1234abcd"
	for _, name := range []string{old, "Transcripts/unregistered-5678abcd", "Transcripts/Readable title"} {
		if err := os.MkdirAll(filepath.Join(root, name), 0700); err != nil {
			t.Fatal(err)
		}
	}
	// Invalid metadata must not override the authoritative database title.
	if err := os.WriteFile(filepath.Join(root, old, "metadata.json"), []byte(`{"title":"Stale title"}`), 0600); err != nil {
		t.Fatal(err)
	}
	title := "Readable title"
	artifact := old
	audio := filepath.Join(root, old, "audio.wav")
	j := models.TranscriptionJob{ID: "owned", VaultID: &v.ID, Title: &title, AudioPath: audio, ArtifactDir: &artifact, Status: models.StatusCompleted}
	if err := db.Create(&j).Error; err != nil {
		t.Fatal(err)
	}
	// An identical relative fragment in another vault must not be rewritten.
	other := models.Vault{Name: "other", Path: t.TempDir()}
	db.Create(&other)
	otherJob := models.TranscriptionJob{ID: "other", VaultID: &other.ID, AudioPath: old + "/audio.wav", Status: models.StatusCompleted}
	db.Create(&otherJob)
	contactOld := "Contacts/People/alice--12345678-1234-1234-1234-123456789abc"
	if err := os.MkdirAll(filepath.Join(root, contactOld), 0700); err != nil {
		t.Fatal(err)
	}
	c := models.Contact{VaultID: v.ID, Name: "Alice", ContactUID: "alice", Slug: "alice", NotePath: contactOld + "/contact.md"}
	db.Create(&c)
	if err := MigrateHumanReadableFolderNames(db); err != nil {
		t.Fatal(err)
	}
	var got models.TranscriptionJob
	db.First(&got, "id = ?", j.ID)
	if got.AudioPath != filepath.Join(root, "Transcripts/Readable title 2/audio.wav") || got.ArtifactDir == nil || *got.ArtifactDir != "Transcripts/Readable title 2" {
		t.Fatalf("incorrect paths: %s %v", got.AudioPath, got.ArtifactDir)
	}
	db.First(&otherJob, "id = ?", otherJob.ID)
	if otherJob.AudioPath != old+"/audio.wav" {
		t.Fatal("changed another vault")
	}
	db.First(&c, c.ID)
	if c.NotePath != "Contacts/People/Alice/contact.md" {
		t.Fatalf("contact path: %s", c.NotePath)
	}
	if _, err := os.Stat(filepath.Join(root, "Transcripts/unregistered-5678abcd")); err != nil {
		t.Fatal("renamed unregistered user folder")
	}
	if err := MigrateHumanReadableFolderNames(db); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "Transcripts/Readable title 2")); err != nil {
		t.Fatal("migration not idempotent")
	}
}

func TestOwnedFolderRejectsPathsOutsideSubtree(t *testing.T) {
	root := t.TempDir()
	for _, path := range []string{"", "../Transcripts/old-1234abcd", "Transcripts/../Elsewhere/old-1234abcd", filepath.Join(filepath.Dir(root), "outside", "Transcripts/old-1234abcd"), "Transcripts"} {
		if got := ownedFolder(root, "Transcripts", path); got != "" {
			t.Errorf("accepted %q as %q", path, got)
		}
	}
	updates := map[string]interface{}{}
	addMigratedPath(updates, "audio_path", "Transcripts/old-1234abcd-extra/audio.wav", root, "Transcripts/old-1234abcd", "Transcripts/New")
	if len(updates) != 0 {
		t.Fatal("rewrote a partial component match")
	}
}

func TestFolderMigrationDoesNotOpenModernOrUnavailableVault(t *testing.T) {
	db := newBackfillTestDB(t)
	// A file cannot be enumerated as a directory; with no legacy DB paths it
	// requires no filesystem access, as with an unavailable cloud archive.
	root := filepath.Join(t.TempDir(), "unavailable")
	if err := os.WriteFile(root, []byte("unchanged"), 0600); err != nil {
		t.Fatal(err)
	}
	v := models.Vault{Name: "unavailable", Path: root}
	db.Create(&v)
	if err := MigrateHumanReadableFolderNames(db); err != nil {
		t.Fatal(err)
	}
}

func TestFolderMigrationRestoresFolderWhenDatabaseUpdateFails(t *testing.T) {
	db := newBackfillTestDB(t)
	root := t.TempDir()
	v := models.Vault{Name: "rollback", Path: root}
	db.Create(&v)
	old := "Transcripts/old-1234abcd"
	os.MkdirAll(filepath.Join(root, old), 0700)
	title := "New title"
	j := models.TranscriptionJob{ID: "rollback", VaultID: &v.ID, Title: &title, AudioPath: old + "/audio.wav", Status: models.StatusCompleted}
	db.Create(&j)
	if err := db.Exec(`CREATE TRIGGER reject_path BEFORE UPDATE OF audio_path ON transcription_jobs BEGIN SELECT RAISE(ABORT, 'test failure'); END`).Error; err != nil {
		t.Fatal(err)
	}
	if err := MigrateHumanReadableFolderNames(db); err == nil {
		t.Fatal("expected database error")
	}
	if _, err := os.Stat(filepath.Join(root, old)); err != nil {
		t.Fatal("original folder not restored")
	}
	if _, err := os.Stat(filepath.Join(root, "Transcripts/New title")); !os.IsNotExist(err) {
		t.Fatal("new folder remains after failed path update")
	}
	db.First(&j, "id = ?", j.ID)
	if j.AudioPath != old+"/audio.wav" {
		t.Fatal("database path changed despite rollback")
	}
}
