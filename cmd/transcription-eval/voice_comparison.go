package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"quill/internal/transcription"
	"quill/internal/transcription/interfaces"
)

// Replay only against the exact assembled machine candidate the verifier read.
func replayVoiceComparison(transcript, evidence string) (*interfaces.TranscriptResult, error) {
	raw, err := os.ReadFile(transcript)
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(evidence)
	if err != nil {
		return nil, err
	}
	var batch struct {
		InputSHA256 string                              `json:"input_sha256"`
		Proposals   []transcription.SpeakerWordProposal `json:"proposals"`
	}
	if err := json.Unmarshal(data, &batch); err != nil {
		return nil, err
	}
	sum := sha256.Sum256(raw)
	if batch.InputSHA256 != hex.EncodeToString(sum[:]) || batch.Proposals == nil {
		return nil, fmt.Errorf("voice comparison does not match the input transcript")
	}
	var source interfaces.TranscriptResult
	if err := json.Unmarshal(raw, &source); err != nil {
		return nil, err
	}
	for _, p := range batch.Proposals {
		if p.Index < 0 || p.Index >= len(source.WordSegments) || source.WordSegments[p.Index].SpeakerReview || p.GapEvidence || p.ReviewChannel != "" {
			return nil, fmt.Errorf("voice comparison requires an assigned machine word")
		}
	}
	return transcription.ApplySpeakerWordProposals(&source, batch.Proposals)
}
