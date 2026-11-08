package transcription

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"quill/internal/models"
	"quill/internal/transcription/interfaces"
)

func annotateRunConfiguration(result *interfaces.TranscriptResult, params models.WhisperXParams) {
	if result.Metadata == nil {
		result.Metadata = map[string]string{}
	}
	// Record effective request settings without credentials, callbacks or API keys.
	config := map[string]interface{}{"model_family": params.ModelFamily, "model": params.Model, "language": params.Language,
		"device": params.Device, "compute_type": params.ComputeType, "batch_size": params.BatchSize,
		"vad_preprocess": params.VadPreprocess, "vad_method": params.VadMethod, "whisper_full_audio": params.WhisperFullAudio,
		"diarization_device": params.DiarizationDevice, "diarize": params.Diarize, "diarize_model": params.DiarizeModel, "min_speakers": params.MinSpeakers, "max_speakers": params.MaxSpeakers,
		"no_align": params.NoAlign, "align_model": params.AlignModel, "vad_onset": params.VadOnset, "vad_offset": params.VadOffset,
		"beam_size": params.BeamSize, "no_speech_threshold": params.NoSpeechThreshold, "condition_on_previous_text": params.ConditionOnPreviousText}
	policy := "engine_native"
	if params.ModelFamily == "whisper" || params.ModelFamily == "" {
		policy = "whisperx_native"
	}
	if params.WhisperFullAudio {
		policy = "original_full_audio"
	} else if params.VadPreprocess {
		policy = "silero_then_engine"
		if params.ModelFamily == "whisper" || params.ModelFamily == "" {
			policy = "silero_then_timestamp_decode"
		}
	}
	config["effective_vad_policy"] = policy
	result.Metadata["vad_policy"] = policy
	raw, _ := json.Marshal(config)
	result.Metadata["pipeline_contract_version"] = "2026-10-29.1"
	result.Metadata["pipeline_request_config"] = string(raw)
	result.Metadata["pipeline_request_sha256"] = fmt.Sprintf("%x", sha256.Sum256(raw))
}
