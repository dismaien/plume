package transcription

import (
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"quill/internal/transcription/interfaces"
	"quill/pkg/binaries"
)

//go:embed wording/check.py
var wordingScript []byte

func (u *UnifiedTranscriptionService) checkWording(ctx context.Context, audio string, result *interfaces.TranscriptResult) (*interfaces.WordingCheck, error) {
	root := filepath.Join(u.envPath, "wording-check")
	python := filepath.Join(root, ".venv", "bin", "python")
	for _, path := range []string{python, filepath.Join(root, "model", "model.safetensors")} {
		if _, err := os.Stat(path); err != nil {
			if os.IsNotExist(err) {
				return &interfaces.WordingCheck{Status: "unavailable"}, nil
			}
			return nil, err
		}
	}
	dir, err := os.MkdirTemp(u.tempDirectory, "wording-*")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)
	source, err := json.Marshal(result)
	if err != nil {
		return nil, err
	}
	input := filepath.Join(dir, "source.json")
	if err := os.WriteFile(input, source, 0600); err != nil {
		return nil, err
	}
	script := filepath.Join(dir, "check.py")
	if err := os.WriteFile(script, wordingScript, 0600); err != nil {
		return nil, err
	}
	pcm := filepath.Join(dir, "audio.pcm")
	if err := exec.CommandContext(ctx, binaries.FFmpeg(), "-nostdin", "-v", "error", "-i", audio, "-vn", "-ar", "16000", "-ac", "1", "-f", "s16le", pcm).Run(); err != nil {
		return nil, fmt.Errorf("decode wording-check audio: %w", err)
	}
	out := filepath.Join(dir, "report.json")
	cmd := exec.CommandContext(ctx, python, script, "--model", filepath.Join(root, "model"), "--pcm", pcm, "--source", input, "--out", out)
	cmd.Env = nativeWordEnvironment()
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("run wording check: %w", err)
	}
	raw, err := os.ReadFile(out)
	if err != nil {
		return nil, err
	}
	var report interfaces.WordingCheck
	if err := json.Unmarshal(raw, &report); err != nil {
		return nil, err
	}
	if report.Status != "complete" || report.Checked+report.Incomplete == 0 {
		return nil, fmt.Errorf("empty wording check")
	}
	return &report, nil
}
