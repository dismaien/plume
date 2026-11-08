package transcription

import (
	"quill/internal/models"
	"testing"
)

func TestLegacySidecarCannotEraseSameNameContactEvidence(t *testing.T) {
	id := uint(18)
	old := []models.SpeakerMapping{{OriginalSpeaker: "A", CustomName: "Alice", ContactID: &id, MatchSource: "manual", ReviewStatus: "accepted", MatchTier: "confirmed", ConfidenceScore: .8}}
	got := preserveLegacyMappingEvidence(old, []models.SpeakerMapping{{OriginalSpeaker: "A", CustomName: "Alice"}})[0]
	if got.ContactID == nil || *got.ContactID != id || got.MatchSource != "manual" || got.ReviewStatus != "accepted" || got.ConfidenceScore != .8 {
		t.Fatalf("lost evidence: %+v", got)
	}
	modern := preserveLegacyMappingEvidence(old, []models.SpeakerMapping{{OriginalSpeaker: "A", CustomName: "Alice", MatchSource: "manual"}})[0]
	if modern.ContactID != nil {
		t.Fatal("ignored explicit modern unlink")
	}
	renamed := preserveLegacyMappingEvidence(old, []models.SpeakerMapping{{OriginalSpeaker: "A", CustomName: "Bob"}})[0]
	if renamed.ContactID != nil || renamed.MatchSource != "" {
		t.Fatal("applied old identity evidence to another name")
	}
}
