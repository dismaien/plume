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
	if result.Metadata["speaker_unattributed_units"] != "2" || result.Metadata["speaker_unit_kind"] != "words" {
		t.Fatal("missing actionable review counts", result.Metadata)
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

func TestPartialAttributionHasAReasonWithoutDiscardingGoodWords(t *testing.T) {
	speaker := "a"
	result := &interfaces.TranscriptResult{Text: "known unclear", WordSegments: []interfaces.TranscriptWord{
		{Start: 0, End: 1, Word: "known", Speaker: &speaker}, {Start: 1, End: 2, Word: "unclear"},
	}}
	annotateOutcomes(result, true, &interfaces.DiarizationResult{SpeakerCount: 1, Metadata: map[string]string{"speaker_identity_status": "accepted"}}, nil)
	if result.Metadata["diarization_reason"] != "partial_speaker_attribution" || result.Metadata["speaker_unattributed_units"] != "1" {
		t.Fatal(result.Metadata)
	}
	if result.WordSegments[0].Speaker == nil || result.Text != "known unclear" {
		t.Fatal("review annotation changed accepted evidence")
	}
}

func TestExclusiveTimelineCannotHideWordLevelOverlap(t *testing.T) {
	source := &interfaces.TranscriptResult{Text: "Known quiet reply.", Segments: []interfaces.TranscriptSegment{{Start: 0, End: 3, Text: "Known quiet reply."}}, WordSegments: []interfaces.TranscriptWord{
		{Start: 0, End: 1, Word: "Known"}, {Start: 1, End: 2, Word: "quiet"}, {Start: 2, End: 3, Word: "reply."},
	}}
	diar := &interfaces.DiarizationResult{SpeakerCount: 2,
		Segments:        []interfaces.DiarizationSegment{{Start: 0, End: 3, Speaker: "a"}},
		RegularSegments: []interfaces.DiarizationSegment{{Start: 0, End: 3, Speaker: "a"}, {Start: 1, End: 2, Speaker: "b"}},
	}
	result := AssembleDiarizedTranscript(source, diar)
	if result.WordSegments[0].Speaker == nil || *result.WordSegments[0].Speaker != "a" || result.WordSegments[2].Speaker == nil {
		t.Fatal("unambiguous words lost their speaker")
	}
	if result.WordSegments[1].Speaker != nil || result.Metadata["diarization_status"] != "uncertain" {
		t.Fatal("exclusive timeline hid an overlapping voice", result.Metadata)
	}
	if result.Text != source.Text || source.WordSegments[0].Speaker != nil || len(result.Diarization.RegularSegments) != 2 {
		t.Fatal("original text, input or acoustic evidence changed")
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
