package pipeline

import (
	"context"
	"errors"
	"strings"
	"testing"

	"quill/internal/transcription/interfaces"
)

type failingPreprocessor struct {
	strict bool
}

func (p *failingPreprocessor) Process(context.Context, interfaces.AudioInput) (interfaces.AudioInput, error) {
	return interfaces.AudioInput{}, errors.New("runtime unavailable")
}

func (p *failingPreprocessor) AppliesTo(interfaces.ModelCapabilities) bool {
	return true
}

func (p *failingPreprocessor) GetRequiredFormats() []string {
	return nil
}

func (p *failingPreprocessor) FailClosed() bool {
	return p.strict
}

func TestProcessAudioFailsWhenRequiredPreprocessorFails(t *testing.T) {
	p := &ProcessingPipeline{}
	p.RegisterPreprocessor(&failingPreprocessor{strict: true})
	input := interfaces.AudioInput{FilePath: "/tmp/original.wav"}

	got, err := p.ProcessAudio(context.Background(), input, interfaces.ModelCapabilities{})
	if err == nil {
		t.Fatal("expected required preprocessor failure to stop processing")
	}
	if !strings.Contains(err.Error(), "required preprocessor") {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.FilePath != input.FilePath {
		t.Fatalf("failed preprocessing should return the current input, got %q", got.FilePath)
	}
}

func TestProcessAudioAllowsOptionalPreprocessorFailure(t *testing.T) {
	p := &ProcessingPipeline{}
	p.RegisterPreprocessor(&failingPreprocessor{strict: false})
	input := interfaces.AudioInput{FilePath: "/tmp/original.wav"}

	got, err := p.ProcessAudio(context.Background(), input, interfaces.ModelCapabilities{})
	if err != nil {
		t.Fatalf("optional preprocessor failure should remain non-fatal: %v", err)
	}
	if got.FilePath != input.FilePath {
		t.Fatalf("optional preprocessing failure should keep original input, got %q", got.FilePath)
	}
}
