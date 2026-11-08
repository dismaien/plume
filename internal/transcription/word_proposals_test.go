package transcription

import (
	"quill/internal/transcription/interfaces"
	"strings"
	"testing"
)

func proposalFixture() (*interfaces.TranscriptResult, SpeakerWordProposal) {
	a := "speaker_0"
	source := &interfaces.TranscriptResult{Text: "Hello,  yeah.", Segments: []interfaces.TranscriptSegment{{Start: 0, End: 1, Text: "Hello,  yeah.", Speaker: &a}}, WordSegments: []interfaces.TranscriptWord{{Start: 0, End: .8, Word: "Hello,", Speaker: &a}, {Start: .8, End: 1, Word: "yeah.", Speaker: &a}}, Metadata: map[string]string{"corrected_text": "Saved correction stays exactly."}}
	p := SpeakerWordProposal{Index: 1, Start: .8, End: 1, Word: "yeah.", Previous: a, Proposed: "speaker_1", Activity: .99, Margin: .8}
	return source, p
}

func TestWordProposalPreservesExactTextAndSplitsTheDisplayedSpeaker(t *testing.T) {
	source, p := proposalFixture()
	result, err := ApplySpeakerWordProposals(source, []SpeakerWordProposal{p})
	if err != nil {
		t.Fatal(err)
	}
	var text strings.Builder
	for _, segment := range result.Segments {
		text.WriteString(segment.Text)
	}
	if result.Text != source.Text || text.String() != source.Text || result.Metadata["corrected_text"] != source.Metadata["corrected_text"] {
		t.Fatal("text or saved correction changed")
	}
	if len(result.Segments) != 2 || result.Segments[1].Speaker == nil || *result.Segments[1].Speaker != "speaker_1" {
		t.Fatal("refined boundary invisible")
	}
	if *source.WordSegments[1].Speaker != "speaker_0" || source.Metadata["diarization_status"] != "" {
		t.Fatal("mutated source")
	}
	if result.Metadata["diarization_status"] != "uncertain" || result.Metadata["speaker_review_start"] != "0.800000" {
		t.Fatal("refinement hid disagreement")
	}
}

func TestWordProposalRejectsUnknownsAndAtomicSnapshotMismatch(t *testing.T) {
	for _, variant := range []string{"unknown", "timing", "text", "low_activity", "duplicate"} {
		t.Run(variant, func(t *testing.T) {
			source, p := proposalFixture()
			batch := []SpeakerWordProposal{p}
			switch variant {
			case "unknown":
				source.WordSegments[1].Speaker = nil
			case "timing":
				batch[0].End = 2
			case "text":
				batch[0].Word = "changed"
			case "low_activity":
				batch[0].Activity = .8
			case "duplicate":
				batch = append(batch, p)
			}
			if _, err := ApplySpeakerWordProposals(source, batch); err == nil {
				t.Fatal("invalid proposal accepted")
			}
			if source.WordSegments[1].Speaker != nil && *source.WordSegments[1].Speaker != "speaker_0" {
				t.Fatal("partial changes escaped")
			}
		})
	}
}
