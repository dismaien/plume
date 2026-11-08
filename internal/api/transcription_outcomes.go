package api

import (
	"encoding/json"
	"quill/internal/models"
	"strings"
)

// Completion is a processing outcome. Naming/publication require usable results.
func TranscriptAllowsAutomaticFollowups(job *models.TranscriptionJob) bool {
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
	if p.Text == "" || p.Metadata["text_status"] == "needs_review" {
		return false
	}
	if !job.Diarization {
		return true
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
