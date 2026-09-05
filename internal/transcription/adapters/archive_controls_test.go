package adapters

import (
	"quill/internal/transcription/interfaces"
	"strings"
	"testing"
)

func TestBuildWhisperXArgsForwardsArchiveControls(t *testing.T) {
	adapter := NewWhisperXAdapter("/tmp/test-env")
	params := map[string]interface{}{"chunk_size": 15, "condition_on_previous_text": false, "compression_ratio_threshold": 2.1, "logprob_threshold": -0.8, "no_speech_threshold": 0.4}
	args, err := adapter.buildWhisperXArgs(interfaces.AudioInput{FilePath: "/tmp/test.wav"}, params, "/tmp/out")
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(args, " ")
	for _, expected := range []string{"--chunk_size 15", "--condition_on_previous_text False", "--compression_ratio_threshold 2.100", "--logprob_threshold -0.800", "--no_speech_threshold 0.400"} {
		if !strings.Contains(joined, expected) {
			t.Errorf("missing %s in %s", expected, joined)
		}
	}
}

func TestBuildWhisperXArgsFullAudioUsesEmbeddedRunner(t *testing.T) {
	adapter := NewWhisperXAdapter("/tmp/test-env")
	params := map[string]interface{}{"whisper_full_audio": true, "temperature_increment_on_fallback": 0.0}
	args, err := adapter.buildWhisperXArgs(interfaces.AudioInput{FilePath: "/tmp/test.wav"}, params, "/tmp/out")
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(args, " ")
	if strings.Contains(joined, "-m whisperx") || !strings.Contains(joined, "python /tmp/out/whisper_full_audio.py /tmp/test.wav") {
		t.Fatalf("full-audio option still invokes the VAD-cutting entry point: %s", joined)
	}
	if !strings.Contains(joined, "--temperature_increment_on_fallback None") {
		t.Fatalf("disabled sampling fallback was not preserved: %s", joined)
	}
	if len(whisperFullAudioScript) == 0 {
		t.Fatal("decoder script is not embedded")
	}
}
