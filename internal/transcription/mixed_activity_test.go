package transcription

import (
	"testing"

	"quill/internal/transcription/interfaces"
)

func TestMixedActivityRetainsShortReplyAndUnresolvedSinging(t *testing.T) {
	d := &interfaces.DiarizationResult{
		Speakers: []string{"A", "B"}, SpeakerCount: 2, Metadata: map[string]string{"speaker_mixed_tracks": `{"X":["A","B"]}`},
		Segments:      []interfaces.DiarizationSegment{{Start: 0, End: 1, Speaker: "A"}, {Start: 1.5, End: 2, Speaker: "A"}},
		MixedSegments: []interfaces.DiarizationSegment{{Start: 1, End: 1.5, Speaker: "X"}, {Start: 4, End: 8, Speaker: "X"}},
	}
	d.RegularSegments = d.Segments
	d.MixedRegularSegments = d.MixedSegments
	src := AssembleDiarizedTranscript(&interfaces.TranscriptResult{
		Text: "one yes two", Segments: []interfaces.TranscriptSegment{{Start: 0, End: 2, Text: "one yes two"}},
		WordSegments: []interfaces.TranscriptWord{{Start: 0, End: 1, Word: "one"}, {Start: 1, End: 1.5, Word: "yes"}, {Start: 1.5, End: 2, Word: "two"}},
	}, d)
	b := AcousticWordRefinement{}
	b.MixedActivity.Exclusive = []MixedActivitySpan{{Index: 0, DiarizationSegment: interfaces.DiarizationSegment{Start: 1, End: 1.5, Speaker: "B"}}}
	b.MixedActivity.Regular = b.MixedActivity.Exclusive
	got, err := ApplyAcousticWordRefinement(src, b)
	if err != nil {
		t.Fatal(err)
	}
	if got.WordSegments[1].Speaker == nil || *got.WordSegments[1].Speaker != "B" || got.Diarization.SpeakerCount != 2 || len(got.Diarization.MixedSegments) != 2 || len(got.Diarization.Segments) != 3 {
		t.Fatal("lost reply, unresolved interval, or roster")
	}
	if src.WordSegments[1].Speaker != nil || len(d.Segments) != 2 {
		t.Fatal("changed input")
	}
	b.MixedActivity.Exclusive[0].End = 9
	if _, err := ApplyAcousticWordRefinement(src, b); err == nil {
		t.Fatal("accepted activity extending beyond its mixed source region")
	}
}
