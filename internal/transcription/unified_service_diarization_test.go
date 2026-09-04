package transcription

import (
	"testing"

	"quill/internal/transcription/interfaces"
)

func speaker(value string) *string {
	return &value
}

func TestMergeDiarizationSplitsWhisperSegmentAtWordSpeakerChanges(t *testing.T) {
	service := &UnifiedTranscriptionService{}
	transcript := &interfaces.TranscriptResult{
		Segments: []interfaces.TranscriptSegment{{
			Start: 0,
			End:   4,
			Text:  "Hello there. Hi back!",
		}},
		WordSegments: []interfaces.TranscriptWord{
			{Start: 0, End: 0.8, Word: "Hello"},
			{Start: 0.9, End: 1.8, Word: "there."},
			{Start: 2.2, End: 2.8, Word: "Hi"},
			{Start: 2.9, End: 3.8, Word: "back!"},
		},
	}
	diarization := &interfaces.DiarizationResult{Segments: []interfaces.DiarizationSegment{
		{Start: 0, End: 2, Speaker: "speaker_0"},
		{Start: 2, End: 4, Speaker: "speaker_1"},
	}}

	merged := service.mergeDiarizationWithTranscription(transcript, diarization)

	if len(merged.Segments) != 2 {
		t.Fatalf("segments = %d, want 2", len(merged.Segments))
	}
	if got := *merged.Segments[0].Speaker; got != "speaker_0" {
		t.Fatalf("first speaker = %q, want speaker_0", got)
	}
	if got := merged.Segments[0].Text; got != "Hello there." {
		t.Fatalf("first text = %q", got)
	}
	if got := *merged.Segments[1].Speaker; got != "speaker_1" {
		t.Fatalf("second speaker = %q, want speaker_1", got)
	}
	if got := merged.Segments[1].Text; got != "Hi back!" {
		t.Fatalf("second text = %q", got)
	}
	if transcript.Segments[0].Speaker != nil {
		t.Fatal("merge mutated the original transcript")
	}
}

func TestSplitSegmentsPreservesOriginalTextWithoutSpeakerChange(t *testing.T) {
	segments := []interfaces.TranscriptSegment{{
		Start:   0,
		End:     2,
		Text:    " Original punctuation -- stays intact. ",
		Speaker: speaker("speaker_0"),
	}}
	words := []interfaces.TranscriptWord{
		{Start: 0, End: 0.7, Word: "Original", Speaker: speaker("speaker_0")},
		{Start: 0.8, End: 1.8, Word: "punctuation", Speaker: speaker("speaker_0")},
	}

	got := splitSegmentsAtWordSpeakerChanges(segments, words)
	if len(got) != 1 || got[0].Text != segments[0].Text {
		t.Fatalf("unchanged segment text = %q, want %q", got[0].Text, segments[0].Text)
	}
}
