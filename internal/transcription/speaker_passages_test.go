package transcription

import (
	"testing"

	"quill/internal/transcription/interfaces"
)

func TestReadableMachineSentenceKeepsEvidenceAndBriefTurns(t *testing.T) {
	for _, tc := range []struct {
		name, middle    string
		rival, disputed bool
		wantSections    int
	}{
		{"uncertain connective", "the", false, false, 1},
		{"unknown backchannel", "yeah", false, false, 3},
		{"overlap-only activity", "the", true, false, 3},
		{"independent disputed channel", "the", false, true, 3},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a := "A"
			parts := []interfaces.TranscriptSegment{{Start: 0, End: .3, Text: "Open "}, {Start: .3, End: .5, Text: tc.middle + " "}, {Start: .5, End: .9, Text: "door."}}
			words := []interfaces.TranscriptWord{{Start: 0, End: .3, Word: "Open", Speaker: &a}, {Start: .3, End: .5, Word: tc.middle, SpeakerReview: tc.disputed}, {Start: .5, End: .9, Word: "door.", Speaker: &a}}
			d := interfaces.DiarizationResult{Segments: []interfaces.DiarizationSegment{{Start: 0, End: .3, Speaker: a}, {Start: .5, End: .9, Speaker: a}}}
			if tc.rival {
				d.RegularSegments = []interfaces.DiarizationSegment{{Start: .4, End: .45, Speaker: "B"}}
			}
			got := machineSpeakerPassages(parts, words, &d)
			if len(got) != tc.wantSections || words[1].Speaker != nil {
				t.Fatalf("passages %v, changed evidence %v", got, words[1])
			}
			if len(got) == 1 && (got[0].Text != "Open the door." || got[0].Start != 0 || got[0].End != .9) {
				t.Fatal(got)
			}
		})
	}
}

func TestDisplayWordSpanningSilenceNeedsSameAcousticEvidenceOnBothSides(t *testing.T) {
	a := "A"
	words := []interfaces.TranscriptWord{{Start: 0, End: 1, Speaker: &a, Word: "felt"}, {Start: 1, End: 3, Word: "confused."}}
	d := &interfaces.DiarizationResult{Segments: []interfaces.DiarizationSegment{{Start: 1, End: 1.6, Speaker: a}}}
	if passageSpeaker(words, 1, d) != nil {
		t.Fatal("borrowed a voice across unsupported silence")
	}
	d.Segments = append(d.Segments, interfaces.DiarizationSegment{Start: 2.6, End: 3, Speaker: a})
	if got := passageSpeaker(words, 1, d); got == nil || *got != a {
		t.Fatal("lost consistent speech across a pause")
	}
	d.RegularSegments = []interfaces.DiarizationSegment{{Start: 1.6, End: 1.7, Speaker: "B"}}
	if passageSpeaker(words, 1, d) != nil {
		t.Fatal("masked an overlapping voice")
	}
}
