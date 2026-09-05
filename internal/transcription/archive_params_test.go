package transcription

import (
	"quill/internal/models"
	"testing"
)

func TestArchiveControlsReachWhisperXAdapter(t *testing.T) {
	service := &UnifiedTranscriptionService{}
	params := models.WhisperXParams{ChunkSize: 15, ConditionOnPreviousText: true, CompressionRatioThreshold: 2.1, LogprobThreshold: -0.8, NoSpeechThreshold: 0.4}
	got := service.convertToWhisperXParams(params)
	for key, want := range map[string]interface{}{"chunk_size": 15, "condition_on_previous_text": true, "compression_ratio_threshold": 2.1, "logprob_threshold": -0.8, "no_speech_threshold": 0.4} {
		if got[key] != want {
			t.Errorf("%s: got %v, want %v", key, got[key], want)
		}
	}
}
