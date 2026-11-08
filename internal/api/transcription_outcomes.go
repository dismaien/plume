package api

import (
	"encoding/json"
	"quill/internal/models"
	"strings"
)

// Text-only titles do not depend on knowing which person said each word.
func TranscriptAllowsAutomaticTextFollowups(job *models.TranscriptionJob) bool {
	if job == nil || job.Transcript == nil || job.CandidateTranscriptRevisionID != nil {
		return false
	}
	var p struct {
		Text     string            `json:"text"`
		Metadata map[string]string `json:"metadata"`
	}
	if json.Unmarshal([]byte(*job.Transcript), &p) != nil {
		return false
	}
	if strings.TrimSpace(p.Text) == "" || (p.Metadata["text_status"] != "" && p.Metadata["text_status"] != "ready") {
		return false
	}
	return true
}

// Person naming, summaries and publication retain their stronger speaker gate.
func TranscriptAllowsAutomaticFollowups(job *models.TranscriptionJob) bool {
	if !TranscriptAllowsAutomaticTextFollowups(job) {
		return false
	}
	if !job.Diarization {
		return true
	}
	var p struct {
		Metadata map[string]string `json:"metadata"`
	}
	if json.Unmarshal([]byte(*job.Transcript), &p) != nil {
		return false
	}
	status := p.Metadata["diarization_status"]
	return status == "accepted" || status == "solo"
}

// A missing token may still mean a usable offline cache. Never silently swap engines.
func (h *Handler) resolveDiarizationToken(params *models.WhisperXParams) {
	if params.DiarizeModel != "pyannote" || hasHFToken(params.HfToken) {
		return
	}
	if h.config != nil && strings.TrimSpace(h.config.HFToken) != "" {
		token := strings.TrimSpace(h.config.HFToken)
		params.HfToken = &token
	}
}
