package transcription

import (
	"errors"
	"quill/internal/models"
	"quill/internal/transcription/interfaces"
	"strings"
	"testing"
)

func TestUncertainDiarizationRetainsEvidenceAndUnknownTurns(t *testing.T) {
	u := &UnifiedTranscriptionService{}
	a := "speaker_0"
	source := &interfaces.TranscriptResult{Text: "Hello,  quiet reply.", Segments: []interfaces.TranscriptSegment{{Start: 0, End: 3, Text: "Hello,  quiet reply."}}, WordSegments: []interfaces.TranscriptWord{{Start: 0, End: 1, Word: "Hello,"}, {Start: 1, End: 2, Word: "quiet"}, {Start: 2, End: 3, Word: "reply."}}}
	diar := &interfaces.DiarizationResult{ModelUsed: "test", SpeakerCount: 1, Metadata: map[string]string{"speaker_identity_status": "uncertain", "speaker_identity_reason": "quiet_voice"}, Segments: []interfaces.DiarizationSegment{{Start: 0, End: 1, Speaker: a}, {Start: 1, End: 3, Speaker: ""}}}
	result := u.mergeDiarizationWithTranscription(source, diar)
	annotateOutcomes(result, true, diar, nil)
	if result.Metadata["diarization_status"] != "uncertain" || result.Diarization == nil || len(result.Diarization.Segments) != 2 {
		t.Fatal("lost outcome/timeline")
	}
	if result.WordSegments[1].Speaker != nil {
		t.Fatal("unknown word acquired identity")
	}
	var combined strings.Builder
	for _, s := range result.Segments {
		combined.WriteString(s.Text)
	}
	if combined.String() != source.Text {
		t.Fatalf("text changed: %q", combined.String())
	}
	if len(result.Segments) != 2 || result.Segments[1].Speaker != nil {
		t.Fatal("unknown boundary hidden by dominant segment speaker")
	}
	payload := &markdownTranscriptPayload{Metadata: result.Metadata, Segments: []markdownTranscriptSegment{{Text: "known", Speaker: &a}, {Text: "unknown"}}}
	if !strings.Contains(renderMarkdownTranscript(&models.TranscriptionJob{}, payload, nil), "**Speaker unclear**") {
		t.Fatal("export hid unknown turn")
	}
}
func TestStageFailureAndEmptyOutputAreNotSuccessOrSilence(t *testing.T) {
	r := &interfaces.TranscriptResult{}
	annotateOutcomes(r, true, nil, errors.New("runtime failed"))
	if r.Metadata["text_status"] != "needs_review" || r.Metadata["diarization_status"] != "failed" {
		t.Fatal(r.Metadata)
	}
}

func TestAmbiguousOverlapAndZeroLengthWords(t *testing.T) {
	u := &UnifiedTranscriptionService{}
	turns := []interfaces.DiarizationSegment{{Start: 0, End: 2, Speaker: "a"}, {Start: 0, End: 2, Speaker: "b"}}
	if got := u.findBestSpeakerForSegment(0, 1, turns); got != "" {
		t.Fatalf("overlap guessed %q", got)
	}
	turns = turns[:1]
	if got := u.findBestSpeakerForSegment(1, 1, turns); got != "a" {
		t.Fatalf("point word lost: %q", got)
	}
	if got := u.findBestSpeakerForSegment(2, 2, turns); got != "" {
		t.Fatal("point word crossed exclusive end boundary")
	}
}

func TestWhisperUsesOneIntentionalVADPolicy(t *testing.T) {
	u := &UnifiedTranscriptionService{}
	for _, p := range []models.WhisperXParams{{VadPreprocess: true}, {WhisperFullAudio: true}, {VadPreprocess: true, WhisperFullAudio: true}} {
		got := u.convertToWhisperXParams(p)
		if got["whisper_full_audio"] != true {
			t.Fatal("outer VAD followed by a second native VAD pass")
		}
	}
}
