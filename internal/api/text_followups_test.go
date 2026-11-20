package api

import (
	"quill/internal/models"
	"testing"
)

func TestTextTitlesRemainAvailableWhenSpeakerAttributionNeedsReview(t *testing.T) {
	for _, status := range []string{"uncertain", "failed"} {
		text := `{"text":"A usable discussion of app settings.","metadata":{"text_status":"ready","diarization_status":"` + status + `"}}`
		job := &models.TranscriptionJob{Transcript: &text, Diarization: true}
		if !TranscriptAllowsAutomaticTextFollowups(job) {
			t.Fatal("speaker uncertainty blocked text title")
		}
		if TranscriptAllowsAutomaticFollowups(job) {
			t.Fatal("uncertain identities allowed automatic naming/publication")
		}
		candidate := uint(1)
		job.CandidateTranscriptRevisionID = &candidate
		if TranscriptAllowsAutomaticTextFollowups(job) {
			t.Fatal("unaccepted candidate allowed automatic work")
		}
	}
}

func TestUnusableTextCannotGetAnAutomaticTitle(t *testing.T) {
	for _, text := range []string{`{"text":"  "}`, `{"text":"decode failed","metadata":{"text_status":"needs_review"}}`, `not-json`} {
		job := &models.TranscriptionJob{Transcript: &text}
		if TranscriptAllowsAutomaticTextFollowups(job) {
			t.Fatal("unusable text passed")
		}
	}
}
