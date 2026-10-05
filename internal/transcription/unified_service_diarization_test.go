package transcription

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"quill/internal/transcription/interfaces"
)

func TestFullAudioInputForDiarizationPreservesConvertedWAVMetadata(t *testing.T) {
	fullPath := filepath.Join(t.TempDir(), "full.wav")
	if err := os.WriteFile(fullPath, []byte("full audio"), 0644); err != nil {
		t.Fatal(err)
	}
	input := interfaces.AudioInput{
		FilePath:     filepath.Join(t.TempDir(), "stripped.wav"),
		Format:       "wav",
		SampleRate:   16000,
		Channels:     1,
		Duration:     2 * time.Hour,
		Size:         3,
		TempFilePath: "stripped.wav",
		Metadata: map[string]string{
			"vad_original_audio": fullPath,
		},
	}

	got, ok := fullAudioInputForDiarization(input)
	if !ok {
		t.Fatal("expected a full-audio diarization input")
	}
	if got.FilePath != fullPath || got.TempFilePath != fullPath {
		t.Fatalf("full audio path was not restored: %+v", got)
	}
	if got.Format != "wav" || got.SampleRate != 16000 || got.Channels != 1 {
		t.Fatalf("converted WAV metadata was not preserved: %+v", got)
	}
	if got.Duration != input.Duration || got.Size != int64(len("full audio")) {
		t.Fatalf("full audio measurements are incorrect: %+v", got)
	}
}

func TestFullAudioInputForDiarizationRequiresVADOriginal(t *testing.T) {
	if _, ok := fullAudioInputForDiarization(interfaces.AudioInput{Metadata: map[string]string{}}); ok {
		t.Fatal("expected no override without VAD original audio metadata")
	}
}

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

func TestSmoothWordSpeakerBoundariesRepairsMicroFlipAndDelayedTurn(t *testing.T) {
	words := []interfaces.TranscriptWord{
		{Start: 53.040, End: 54.359, Word: "Okay,", Speaker: speaker("speaker_0")},
		{Start: 54.379, End: 54.420, Word: "so", Speaker: speaker("speaker_0")},
		{Start: 54.440, End: 54.521, Word: "like", Speaker: speaker("speaker_0")},
		{Start: 54.562, End: 54.643, Word: "an", Speaker: speaker("speaker_0")},
		{Start: 54.704, End: 55.029, Word: "hour", Speaker: speaker("speaker_0")},
		{Start: 55.171, End: 55.252, Word: "or", Speaker: speaker("speaker_1")},
		{Start: 55.414, End: 55.597, Word: "something", Speaker: speaker("speaker_0")},
		{Start: 55.617, End: 55.699, Word: "like", Speaker: speaker("speaker_0")},
		{Start: 55.719, End: 55.820, Word: "that.", Speaker: speaker("speaker_0")},
		{Start: 55.800, End: 56.236, Word: "Let", Speaker: speaker("speaker_0")},
		{Start: 56.340, End: 56.381, Word: "me", Speaker: speaker("speaker_0")},
		{Start: 56.443, End: 56.630, Word: "check", Speaker: speaker("speaker_1")},
		{Start: 56.672, End: 56.921, Word: "that.", Speaker: speaker("speaker_1")},
	}

	segments := []interfaces.TranscriptSegment{
		{Start: 53.040, End: 55.820, Text: "Okay, so like an hour or something like that.", Speaker: speaker("speaker_0")},
		{Start: 55.800, End: 56.921, Text: "Let me check that.", Speaker: speaker("speaker_0")},
		{Start: 60.560, End: 64.520, Text: "yeah there's some kind of all hands meeting.", Speaker: speaker("speaker_1")},
	}

	smoothWordSpeakerBoundaries(segments, words)

	for i := 0; i <= 8; i++ {
		if got := transcriptWordSpeaker(words[i]); got != "speaker_0" {
			t.Fatalf("word %q speaker = %q, want speaker_0", words[i].Word, got)
		}
	}
	for i := 9; i < len(words); i++ {
		if got := transcriptWordSpeaker(words[i]); got != "speaker_1" {
			t.Fatalf("word %q speaker = %q, want speaker_1", words[i].Word, got)
		}
	}

	split := splitSegmentsAtWordSpeakerChanges(segments, words)
	if len(split) != 3 {
		t.Fatalf("segments = %d, want 3: %+v", len(split), split)
	}
	if got := split[0].Text; got != "Okay, so like an hour or something like that." {
		t.Fatalf("first segment = %q", got)
	}
	if got := split[1].Text; got != "Let me check that." {
		t.Fatalf("second segment = %q", got)
	}
}

func TestSmoothWordSpeakerBoundariesPreservesRealInterjection(t *testing.T) {
	words := []interfaces.TranscriptWord{
		{Start: 0.00, End: 0.25, Word: "I", Speaker: speaker("speaker_0")},
		{Start: 0.27, End: 0.60, Word: "think", Speaker: speaker("speaker_0")},
		{Start: 0.65, End: 0.91, Word: "yeah.", Speaker: speaker("speaker_1")},
		{Start: 0.96, End: 1.18, Word: "we", Speaker: speaker("speaker_0")},
		{Start: 1.20, End: 1.48, Word: "should", Speaker: speaker("speaker_0")},
		{Start: 1.50, End: 1.82, Word: "continue.", Speaker: speaker("speaker_0")},
	}

	smoothWordSpeakerBoundaries(nil, words)

	if got := transcriptWordSpeaker(words[2]); got != "speaker_1" {
		t.Fatalf("interjection speaker = %q, want speaker_1", got)
	}
}

func TestSmoothWordSpeakerBoundariesPreservesUnpunctuatedBackchannel(t *testing.T) {
	words := []interfaces.TranscriptWord{
		{Start: 0.00, End: 0.25, Word: "I", Speaker: speaker("speaker_0")},
		{Start: 0.27, End: 0.60, Word: "think", Speaker: speaker("speaker_0")},
		{Start: 0.65, End: 0.73, Word: "yeah", Speaker: speaker("speaker_1")},
		{Start: 0.77, End: 0.99, Word: "we", Speaker: speaker("speaker_0")},
		{Start: 1.01, End: 1.29, Word: "should", Speaker: speaker("speaker_0")},
	}

	smoothWordSpeakerBoundaries(nil, words)

	if got := transcriptWordSpeaker(words[2]); got != "speaker_1" {
		t.Fatalf("backchannel speaker = %q, want speaker_1", got)
	}
}

func TestSmoothWordSpeakerBoundariesPreservesBackchannelAtPossibleBoundary(t *testing.T) {
	words := []interfaces.TranscriptWord{
		{Start: 0.00, End: 0.18, Word: "Yeah,", Speaker: speaker("speaker_0")},
		{Start: 0.21, End: 0.43, Word: "we", Speaker: speaker("speaker_1")},
		{Start: 0.45, End: 0.73, Word: "should", Speaker: speaker("speaker_1")},
		{Start: 0.75, End: 1.05, Word: "continue.", Speaker: speaker("speaker_1")},
	}
	segments := []interfaces.TranscriptSegment{
		{Start: -1.0, End: -0.1, Text: "Previous thought.", Speaker: speaker("speaker_0")},
		{Start: 0.0, End: 1.05, Text: "Yeah, we should continue.", Speaker: speaker("speaker_0")},
		{Start: 1.2, End: 2.0, Text: "Next thought.", Speaker: speaker("speaker_1")},
	}

	smoothWordSpeakerBoundaries(segments, words)

	if got := transcriptWordSpeaker(words[0]); got != "speaker_0" {
		t.Fatalf("boundary backchannel speaker = %q, want speaker_0", got)
	}
}
