package transcription

import (
	"quill/internal/transcription/interfaces"
	"strings"
	"testing"
)

func proposalFixture() (*interfaces.TranscriptResult, SpeakerWordProposal) {
	a := "speaker_0"
	source := &interfaces.TranscriptResult{Text: "Hello,  yeah.", Segments: []interfaces.TranscriptSegment{{Start: 0, End: 1, Text: "Hello,  yeah.", Speaker: &a}}, WordSegments: []interfaces.TranscriptWord{{Start: 0, End: .8, Word: "Hello,", Speaker: &a}, {Start: .8, End: 1, Word: "yeah.", Speaker: &a}}, Metadata: map[string]string{"corrected_text": "Saved correction stays exactly."}}
	source.Diarization = &interfaces.DiarizationResult{RegularSegments: []interfaces.DiarizationSegment{{Start: 0, End: 1, Speaker: a}, {Start: .8, End: 1, Speaker: "speaker_1"}}}
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
	for _, variant := range []string{"unknown", "timing", "text", "low_activity", "duplicate", "invented"} {
		t.Run(variant, func(t *testing.T) {
			source, p := proposalFixture()
			batch := []SpeakerWordProposal{p}
			switch variant {
			case "unknown":
				source.WordSegments[1].Speaker = nil
			case "invented":
				batch[0].Proposed = "speaker_5"
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

func TestUnmatchedChannelPreservesAReviewableTurnWithoutAddingAPerson(t *testing.T) {
	source, p := proposalFixture()
	p.Proposed, p.ReviewChannel = "", "native_3"
	result, err := ApplySpeakerWordProposals(source, []SpeakerWordProposal{p})
	if err != nil {
		t.Fatal(err)
	}
	if !result.WordSegments[1].SpeakerReview || result.WordSegments[1].Speaker != nil || len(result.Segments) != 2 || result.Segments[1].Speaker != nil {
		t.Fatal("disputed reply must keep its own reviewable boundary")
	}
	if result.Metadata["speaker_attributed_count"] != "1" || result.Metadata["speaker_unattributed_units"] != "1" || result.Metadata["word_refinement_unmatched_words"] != "1" {
		t.Fatalf("incorrect review accounting: %v", result.Metadata)
	}
}

func TestGapProposalRequiresAFreshUnassignedWordAndKnownIdentity(t *testing.T) {
	src, p := proposalFixture()
	src.WordSegments[1].Speaker = nil
	p.GapEvidence, p.Previous = true, ""
	p.Activity, p.PrimaryFraction = .71, .95
	result, err := ApplyAcousticWordRefinement(src, AcousticWordRefinement{Proposals: []SpeakerWordProposal{p}})
	if err != nil || result.WordSegments[1].Speaker == nil || *result.WordSegments[1].Speaker != p.Proposed || src.WordSegments[1].Speaker != nil {
		t.Fatal("failed to recover an independently supported gap on a copy", err)
	}
	src.WordSegments[1].SpeakerReview = true
	if _, err := ApplySpeakerWordProposals(src, []SpeakerWordProposal{p}); err == nil {
		t.Fatal("filled a disputed word")
	}
	src.WordSegments[1].SpeakerReview = false
	p.Proposed = "unseen"
	if _, err := ApplySpeakerWordProposals(src, []SpeakerWordProposal{p}); err == nil {
		t.Fatal("invented a voice")
	}
	p.Proposed, p.PrimaryFraction = "speaker_1", .2
	if _, err := ApplySpeakerWordProposals(src, []SpeakerWordProposal{p}); err == nil {
		t.Fatal("weak activity without primary support")
	}
}

func TestOverlappingWordTimesDoNotConsumeTheNextReply(t *testing.T) {
	a, b := "A", "B"
	words := []interfaces.TranscriptWord{{Start: 0, End: .4, Word: "I", Speaker: &a}, {Start: .4, End: 1, Word: "think.", Speaker: &a}, {Start: .92, End: 1, Word: "Yes.", Speaker: &b}}
	rows := []interfaces.TranscriptSegment{{Start: 0, End: 1, Text: " I think."}, {Start: .92, End: 1, Text: " Yes."}}
	got := splitSegmentsAtWordSpeakerChanges(rows, words)
	if len(got) != 2 || got[0].Speaker == nil || *got[0].Speaker != a || got[1].Speaker == nil || *got[1].Speaker != b || got[0].Text+got[1].Text != " I think. Yes." {
		t.Fatal("overlapping word consumed another utterance", got)
	}
}
