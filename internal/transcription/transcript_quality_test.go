package transcription

import (
	"fmt"
	"strings"
	"testing"

	"quill/internal/transcription/interfaces"
)

func TestValidateTranscriptQualityRejectsHighNoSpeechFiller(t *testing.T) {
	diagnostics := `{"segments":[
		{"text":"Thank you.","no_speech_prob":0.77},
		{"text":"Thank you.","no_speech_prob":0.81},
		{"text":"Thank you.","no_speech_prob":0.74}
	]}`
	result := &interfaces.TranscriptResult{
		Text:     "Some real speech. Thank you. More real speech. Thank you. Thank you.",
		Metadata: map[string]string{"asr_diagnostics": diagnostics},
	}

	err := validateTranscriptQuality(result)
	if err == nil || !strings.Contains(err.Error(), "high-no-speech segments") {
		t.Fatalf("expected high-no-speech filler rejection, got %v", err)
	}
}

func TestValidateTranscriptQualityRejectsCatastrophicFillerBurden(t *testing.T) {
	result := &interfaces.TranscriptResult{
		Text:     "actual speech " + strings.Repeat("Thank you. ", minimumFillerOccurrences) + "more actual speech",
		Metadata: map[string]string{},
	}

	err := validateTranscriptQuality(result)
	if err == nil || !strings.Contains(err.Error(), "repeated filler hallucination") {
		t.Fatalf("expected repeated filler rejection, got %v", err)
	}
}

func TestValidateTranscriptQualityAllowsIsolatedThanks(t *testing.T) {
	result := &interfaces.TranscriptResult{
		Text: "Thank you for coming today. We discussed the project and agreed on the next steps.",
		Metadata: map[string]string{"asr_diagnostics": `{"segments":[
			{"text":"Thank you.","no_speech_prob":0.1},
			{"text":"We discussed the project.","no_speech_prob":0.02}
		]}`},
	}

	if err := validateTranscriptQuality(result); err != nil {
		t.Fatalf("expected legitimate thanks to pass, got %v", err)
	}
}

func TestValidateTranscriptQualityAllowsSubstantiveRepetition(t *testing.T) {
	result := &interfaces.TranscriptResult{
		Text:     fmt.Sprintf("Why %s does this matter so much?", strings.Repeat("why ", 20)),
		Metadata: map[string]string{},
	}

	if err := validateTranscriptQuality(result); err != nil {
		t.Fatalf("expected substantive repetition to pass, got %v", err)
	}
}
