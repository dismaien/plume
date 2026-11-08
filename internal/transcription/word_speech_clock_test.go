package transcription

import (
	"quill/internal/transcription/interfaces"
	"quill/internal/transcription/pipeline"
	"testing"
)

func TestWordSpeechClockPreservesSameVoiceAcrossRemovedPause(t *testing.T) {
	u := &UnifiedTranscriptionService{}
	word := interfaces.TranscriptWord{Start: .8, End: 11.2, Word: "that"}
	regions := []pipeline.SpeechRegion{{Start: 0, End: 1}, {Start: 11, End: 12}}
	turns := []interfaces.DiarizationSegment{{Start: 0, End: 1, Speaker: "A"}, {Start: 11, End: 12, Speaker: "A"}}
	if got := u.findBestSpeakerForSegment(word.Start, word.End, turns); got != "" {
		t.Fatal("fixture must expose pause dilution")
	}
	if got := u.findWordSpeakerWithSpeechClock(word, turns, turns, regions, regions); got != "A" {
		t.Fatalf("got %q", got)
	}
	if word.Start != .8 || word.End != 11.2 || word.Word != "that" {
		t.Fatal("changed source word")
	}
	turns[1].Speaker = "B"
	if got := u.findWordSpeakerWithSpeechClock(word, turns, turns, regions, regions); got != "" {
		t.Fatal("swallowed another person across splice")
	}
}
func TestWordSpeechClockKeepsRegularOverlapAmbiguous(t *testing.T) {
	u := &UnifiedTranscriptionService{}
	word := interfaces.TranscriptWord{Start: .8, End: 11.2}
	regions := []pipeline.SpeechRegion{{Start: 0, End: 1}, {Start: 11, End: 12}}
	primary := []interfaces.DiarizationSegment{{Start: 0, End: 12, Speaker: "A"}}
	regular := append([]interfaces.DiarizationSegment{}, primary...)
	regular = append(regular, interfaces.DiarizationSegment{Start: 0, End: 12, Speaker: "B"})
	if got := u.findWordSpeakerWithSpeechClock(word, primary, regular, regions, regions); got != "" {
		t.Fatalf("overlap assigned to %q", got)
	}
	for _, raw := range []string{`[{"start":0,"end":1},{"start":0.5,"end":2}]`, `[{"start":2,"end":1}]`, `not json`} {
		if retainedSpeechRegions(raw) != nil {
			t.Fatal("accepted invalid evidence")
		}
	}
}

func TestSpeechClockOneAcousticPieceAfterARealVadCut(t *testing.T) {
	u := &UnifiedTranscriptionService{}
	word := interfaces.TranscriptWord{Start: .8, End: 11.2, Word: "I"}
	retained := []pipeline.SpeechRegion{{Start: 0, End: 1}, {Start: 11, End: 12}}
	speech := []pipeline.SpeechRegion{{Start: 11.05, End: 11.2}}
	turns := []interfaces.DiarizationSegment{{Start: 11.05, End: 11.2, Speaker: "A"}}
	if got := u.findWordSpeakerWithSpeechClock(word, turns, turns, speech, retained); got != "A" {
		t.Fatalf("got %q", got)
	}
	// The same evidence cannot fill a generic unknown when no splice is proven.
	if got := u.findWordSpeakerWithSpeechClock(word, turns, turns, speech, nil); got != "" {
		t.Fatal("masked unsupported time without VAD provenance")
	}
}
