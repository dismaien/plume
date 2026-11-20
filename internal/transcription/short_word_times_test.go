package transcription

import (
	"quill/internal/transcription/interfaces"
	"testing"
)

func TestIndependentWordTimeNeedsUnopposedAcousticCoverage(t *testing.T) {
	src, _ := proposalFixture()
	src.WordSegments[1].Speaker = nil
	track := []interfaces.DiarizationSegment{{Start: .92, End: 1.1, Speaker: "speaker_1"}}
	src.Diarization.RegularSegments = track
	src.Diarization.Segments = track
	obs := WordTimeObservations{Observed: []WordTimeObservation{{Index: 1, Word: "yeah.", Start: .8, End: 1, Onset: .94, Offset: 1.04}}}
	got, err := ApplyObservedWordTimes(src, obs)
	if err != nil || got.WordSegments[1].Speaker == nil || *got.WordSegments[1].Speaker != "speaker_1" || src.WordSegments[1].Speaker != nil ||
		got.WordSegments[1].Start != .8 || got.WordSegments[1].End != 1 || got.Text != src.Text {
		t.Fatal("invalid recovery", err)
	}
	src.Diarization.RegularSegments = append(src.Diarization.RegularSegments, interfaces.DiarizationSegment{Start: 1.01, End: 1.02, Speaker: "speaker_0"})
	got, err = ApplyObservedWordTimes(src, obs)
	if err != nil || got.WordSegments[1].Speaker != nil {
		t.Fatal("overlap was given a speaker")
	}
	obs.Observed[0].Start = .7
	if _, err = ApplyObservedWordTimes(src, obs); err == nil {
		t.Fatal("wrong source word was accepted")
	}
}

func TestExcerptCannotLendOneReplyToTwoASRTokens(t *testing.T) {
	words := []interfaces.TranscriptWord{{Word: "oh", Start: 0, End: .25}, {Word: "yeah", Start: .3, End: .5}, {Word: "Yeah.", Start: .45, End: .68}}
	if !overlapsRepeatedWord(words, WordTimeObservation{Index: 1, Word: "yeah", Onset: .38, Offset: .62}) {
		t.Fatal("one decoded sound was attributed to both saved words")
	}
}

func TestObservedWordAtHandoffNeedsIndependentSpeakerWitness(t *testing.T) {
	src, _ := proposalFixture()
	w := &src.WordSegments[1]
	w.Speaker = nil
	w.Start, w.End = .5, 1.5
	track := []interfaces.DiarizationSegment{{Start: .4, End: .9, Speaker: "speaker_0"}, {Start: 1, End: 1.7, Speaker: "speaker_1"}}
	src.Diarization.RegularSegments = track
	src.Diarization.Segments = track
	for _, tc := range []struct{ witness, want string }{{"", ""}, {"speaker_0", ""}, {"speaker_1", "speaker_1"}} {
		obs := WordTimeObservations{Observed: []WordTimeObservation{{Index: 1, Word: w.Word, Start: w.Start, End: w.End, Onset: 1.05, Offset: 1.35, NativeSpeaker: tc.witness}}}
		got, err := ApplyObservedWordTimes(src, obs)
		if err != nil {
			t.Fatal(err)
		}
		id := ""
		if speaker := got.WordSegments[1].Speaker; speaker != nil {
			id = *speaker
		}
		if id != tc.want || w.Speaker != nil {
			t.Fatalf("witness %q gave %q, want %q", tc.witness, id, tc.want)
		}
	}
}
