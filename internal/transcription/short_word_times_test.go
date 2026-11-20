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
