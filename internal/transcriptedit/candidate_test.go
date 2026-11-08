package transcriptedit

import (
	"context"
	"errors"
	"quill/internal/models"
	"quill/internal/repository"
	"testing"
)

func TestRerunCandidateAcceptAndMappingRollback(t *testing.T) {
	s, _ := setup(t)
	ctx := context.Background()
	if err := s.DB.AutoMigrate(&models.SpeakerMapping{}); err != nil {
		t.Fatal(err)
	}
	mapping := models.SpeakerMapping{TranscriptionJobID: "one", OriginalSpeaker: "speaker_0", CustomName: "Leona", MatchSource: "manual"}
	if err := s.DB.Create(&mapping).Error; err != nil {
		t.Fatal(err)
	}
	candidate := `{"text":"replacement","segments":[{"start":0,"end":1,"text":"replacement","speaker":"speaker_0"}]}`
	repo := repository.NewJobRepository(s.DB)
	if err := s.DB.Model(&models.TranscriptionJob{}).Where("id = ?", "one").Update("diarization", true).Error; err != nil {
		t.Fatal(err)
	}
	if err := repo.UpdateTranscript(ctx, "one", candidate); err != nil {
		t.Fatal(err)
	}
	job, err := repo.FindByID(ctx, "one")
	if err != nil {
		t.Fatal(err)
	}
	if job.Transcript == nil || *job.Transcript != original || job.CandidateTranscriptRevisionID == nil {
		t.Fatal("accepted result overwritten")
	}
	candidateID := *job.CandidateTranscriptRevisionID
	var before models.TranscriptRevision
	if err := s.DB.Where("kind = ?", "before_rerun").First(&before).Error; err != nil {
		t.Fatal(err)
	}
	doc := current(t, s, "one")
	if _, err := s.Save(ctx, "one", Request{ExpectedVersion: doc.Version, RestoreID: &candidateID}); err != nil {
		t.Fatal(err)
	}
	var count int64
	s.DB.Model(&models.SpeakerMapping{}).Count(&count)
	acceptedJob, err := repo.FindByID(ctx, "one")
	if err != nil {
		t.Fatal(err)
	}
	if acceptedJob.Diarization {
		t.Fatal("accepted result kept previous run request flag")
	}
	if count != 0 {
		t.Fatal("numeric speaker IDs inherited old names")
	}
	doc = current(t, s, "one")
	if string(doc.Transcript) != candidate {
		t.Fatal("candidate not accepted")
	}
	if _, err := s.Save(ctx, "one", Request{ExpectedVersion: doc.Version, RestoreID: &before.ID}); err != nil {
		t.Fatal(err)
	}
	restoredJob, err := repo.FindByID(ctx, "one")
	if err != nil {
		t.Fatal(err)
	}
	if !restoredJob.Diarization {
		t.Fatal("rollback lost original diarization request flag")
	}
	var restored models.SpeakerMapping
	if err := s.DB.First(&restored).Error; err != nil {
		t.Fatal(err)
	}
	if restored.CustomName != "Leona" || restored.MatchSource != "manual" || string(current(t, s, "one").Transcript) != original {
		t.Fatal("rollback did not recover text and names")
	}
}

func TestCandidateRejectsChangedSpeakerMapping(t *testing.T) {
	s, _ := setup(t)
	ctx := context.Background()
	s.DB.AutoMigrate(&models.SpeakerMapping{})
	if err := repository.NewJobRepository(s.DB).UpdateTranscript(ctx, "one", `{"text":"candidate"}`); err != nil {
		t.Fatal(err)
	}
	var candidate models.TranscriptRevision
	s.DB.Where("kind = ?", "candidate").First(&candidate)
	s.DB.Create(&models.SpeakerMapping{TranscriptionJobID: "one", OriginalSpeaker: "speaker_0", CustomName: "New correction", MatchSource: "manual"})
	doc := current(t, s, "one")
	_, err := s.Save(ctx, "one", Request{ExpectedVersion: doc.Version, RestoreID: &candidate.ID})
	if !errors.Is(err, ErrConflict) {
		t.Fatalf("expected conflict, got %v", err)
	}
	if string(current(t, s, "one").Transcript) != original {
		t.Fatal("conflict overwrote accepted text")
	}
}
