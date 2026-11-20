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

func TestModelSoloVerdictDoesNotConfirmUnobservedWords(t *testing.T) {
	source := &interfaces.TranscriptResult{Text: "quiet reply", WordSegments: []interfaces.TranscriptWord{{Start: 2, End: 2.3, Word: "quiet"}}, Segments: []interfaces.TranscriptSegment{{Start: 2, End: 2.3, Text: "quiet reply"}}}
	diar := &interfaces.DiarizationResult{Segments: []interfaces.DiarizationSegment{{Start: 0, End: 1, Speaker: "A"}}, Speakers: []string{"A"}, Metadata: map[string]string{"speaker_identity_status": "solo"}}
	result := AssembleDiarizedTranscript(source, diar)
	if result.WordSegments[0].Speaker != nil {
		t.Fatal("model solo claim filled unsupported speech")
	}
}

func TestUnopposedWordEdgeRequiresBothTimelinesAndKeepsReplies(t *testing.T) {
	word := interfaces.TranscriptWord{Start: 1, End: 1.4, Word: "yeah"}
	a := interfaces.DiarizationSegment{Start: 1.25, End: 2, Speaker: "reply"}
	sole := []interfaces.DiarizationSegment{a}
	if got := unopposedWordEdge(word, sole, sole); got != "reply" {
		t.Fatalf("lost positive evidence for brief reply: %q", got)
	}
	competing := append([]interfaces.DiarizationSegment{a}, interfaces.DiarizationSegment{Start: 1.01, End: 1.03, Speaker: "other"})
	for _, tracks := range [][][]interfaces.DiarizationSegment{{sole, competing}, {competing, sole}, {sole, nil}, {nil, sole}} {
		if got := unopposedWordEdge(word, tracks[0], tracks[1]); got != "" {
			t.Fatalf("claimed disputed or missing support: %q", got)
		}
	}
}

func TestWordEdgeUnionsTouchingRestoredTurnsWithinFrameResolution(t *testing.T) {
	reply := interfaces.TranscriptWord{Start: 1, End: 1.402, Word: "yes"}
	turns := []interfaces.DiarizationSegment{{Start: 1.319, End: 1.56, Speaker: "respondent"}, {Start: 1.302, End: 1.319, Speaker: "respondent"}}
	if got := unopposedWordEdge(reply, turns, turns); got != "respondent" {
		t.Fatalf("lost the split acoustic edge: %q", got)
	}
}

func TestIsolatedBackchannelNeedsAnUncontestedTurnOfItsOwn(t *testing.T) {
	a := "A"
	base := []interfaces.TranscriptWord{{Start: 0, End: 1, Word: "here", Speaker: &a}, {Start: 1.4, End: 2.1, Word: "yeah"}, {Start: 2.11, End: 2.4, Word: "again", Speaker: &a}}
	turns := []interfaces.DiarizationSegment{{Start: 0, End: 1.28, Speaker: "A"}, {Start: 1.29, End: 1.62, Speaker: "B"}, {Start: 1.94, End: 2.4, Speaker: "A"}}
	words := append([]interfaces.TranscriptWord(nil), base...)
	restoreIsolatedReplies(words, turns, turns)
	if words[1].Speaker == nil || *words[1].Speaker != "B" {
		t.Fatal("lost reply across the restart")
	}
	both := append(append([]interfaces.DiarizationSegment{}, turns...), interfaces.DiarizationSegment{Start: 1.35, End: 1.4, Speaker: "C"})
	restoreIsolatedReplies(base, turns, both)
	if base[1].Speaker != nil {
		t.Fatal("hid overlap")
	}
}
