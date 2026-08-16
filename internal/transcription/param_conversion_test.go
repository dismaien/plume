package transcription

import (
	"testing"

	"quill/internal/models"
)

func TestConvertToSortformerParams_MaxSpeakersIncluded(t *testing.T) {
	svc := &UnifiedTranscriptionService{}

	maxSpeakers := 2
	params := models.WhisperXParams{
		MaxSpeakers: &maxSpeakers,
	}

	result := svc.convertToSortformerParams(params)

	val, ok := result["max_speakers"]
	if !ok {
		t.Fatal("expected max_speakers in result map, but it was missing")
	}
	if val != 2 {
		t.Errorf("expected max_speakers=2, got %v", val)
	}
}

func TestConvertToSortformerParams_MaxSpeakersOmittedWhenNil(t *testing.T) {
	svc := &UnifiedTranscriptionService{}

	params := models.WhisperXParams{}

	result := svc.convertToSortformerParams(params)

	if _, ok := result["max_speakers"]; ok {
		t.Error("expected max_speakers to be absent when nil, but it was present")
	}
}

func TestConvertToSortformerParams_AlwaysIncludesDefaults(t *testing.T) {
	svc := &UnifiedTranscriptionService{}

	result := svc.convertToSortformerParams(models.WhisperXParams{})

	if result["output_format"] != OutputFormatJSON {
		t.Errorf("expected output_format=%s, got %v", OutputFormatJSON, result["output_format"])
	}
	if result["auto_convert_audio"] != true {
		t.Errorf("expected auto_convert_audio=true, got %v", result["auto_convert_audio"])
	}
}

func TestConvertToMLXWhisperParams_IncludesQualityGuards(t *testing.T) {
	svc := &UnifiedTranscriptionService{}

	prompt := "Use speaker names Leona, Abraham, Andre."
	params := models.WhisperXParams{
		Model:                     "large-v3-turbo",
		Task:                      "transcribe",
		Temperature:               0,
		ConditionOnPreviousText:   false,
		CompressionRatioThreshold: 2.4,
		LogprobThreshold:          -1.0,
		NoSpeechThreshold:         0.6,
		InitialPrompt:             &prompt,
	}

	result := svc.convertToMLXWhisperParams(params)

	expected := map[string]interface{}{
		"model":                       "large-v3-turbo",
		"task":                        "transcribe",
		"temperature":                 0.0,
		"condition_on_previous_text":  false,
		"compression_ratio_threshold": 2.4,
		"logprob_threshold":           -1.0,
		"no_speech_threshold":         0.6,
		"word_timestamps":             true,
		"initial_prompt":              prompt,
	}

	for key, want := range expected {
		if got := result[key]; got != want {
			t.Errorf("expected %s=%v, got %v", key, want, got)
		}
	}
}
