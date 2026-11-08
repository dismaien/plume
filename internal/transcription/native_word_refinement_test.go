package transcription

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"quill/internal/transcription/interfaces"
)

func TestUnverifiedNativeProfileCannotChangeThePrimaryResult(t *testing.T) {
	root := t.TempDir()
	native := filepath.Join(root, "nemotron3")
	if err := os.MkdirAll(native, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(native, "ready.json"), []byte(`{"enabled":true,"model_revision":"unverified","policy":"known_machine_labels_v1"}`), 0600); err != nil {
		t.Fatal(err)
	}
	u := &UnifiedTranscriptionService{envPath: root}
	source, _ := proposalFixture()
	result, err := u.refineNativeWords(context.Background(), interfaces.AudioInput{}, source, interfaces.ProcessingContext{})
	if err == nil || result != source || *source.WordSegments[1].Speaker != "speaker_0" {
		t.Fatal("profile failure changed primary result")
	}
}

func TestNativeSubprocessIsOfflineAndDoesNotInheritProviderCredentials(t *testing.T) {
	t.Setenv("HF_TOKEN", "test-secret")
	t.Setenv("OPENAI_API_KEY", "test-secret")
	t.Setenv("PYTHONPATH", "/unrelated/packages")
	t.Setenv("PLUME_EVAL_CPU_ASR_PERMIT", "/expired/permit")
	environment := nativeWordEnvironment()
	joined := strings.Join(environment, "\n")
	for _, name := range []string{"HF_TOKEN=", "OPENAI_API_KEY=", "PYTHONPATH=", "PLUME_EVAL_CPU_ASR_PERMIT="} {
		if strings.Contains(joined, name) {
			t.Fatal("unnecessary ambient state inherited", name)
		}
	}
	if !strings.Contains(joined, "HF_HUB_OFFLINE=1") || !strings.Contains(joined, "PYTORCH_ENABLE_MPS_FALLBACK=0") {
		t.Fatal("offline/device policy missing")
	}
}
