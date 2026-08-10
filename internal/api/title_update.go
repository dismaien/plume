package api

import (
	"context"
	"os"
	"strings"

	"quill/internal/models"
	"quill/internal/transcription"
	"quill/pkg/logger"
)

func (h *Handler) applyTranscriptionTitle(ctx context.Context, job *models.TranscriptionJob, title string) error {
	newTitle := strings.TrimSpace(title)
	if newTitle == "" {
		return nil
	}

	originalTitle := job.Title
	if originalTitle != nil && strings.TrimSpace(*originalTitle) == newTitle {
		return nil
	}

	// Capture original paths for rollback on DB failure.
	originalArtifactDir := ""
	if job.ArtifactDir != nil {
		originalArtifactDir = *job.ArtifactDir
	}
	originalAudioPath := job.AudioPath
	var originalJSONPath, originalMDPath *string
	if job.TranscriptJSONPath != nil {
		v := *job.TranscriptJSONPath
		originalJSONPath = &v
	}
	if job.TranscriptMarkdownPath != nil {
		v := *job.TranscriptMarkdownPath
		originalMDPath = &v
	}

	job.Title = &newTitle

	// Rename bundle directory on disk to reflect the new title. This is
	// best-effort; the DB title should still update if the filesystem rename is
	// blocked by a missing folder or destination collision.
	bundleRenamed := false
	if job.ArtifactDir != nil && *job.ArtifactDir != "" {
		if result, renameErr := transcription.RenameBundleDir(*job.ArtifactDir, newTitle, job.ID); renameErr != nil {
			logger.Warn("Failed to rename bundle dir for title update",
				"job_id", job.ID, "error", renameErr)
		} else if result.NewDir != originalArtifactDir {
			bundleRenamed = true
			job.ArtifactDir = &result.NewDir
			if result.AudioPath != "" {
				job.AudioPath = result.AudioPath
			}
			if result.JSONPath != "" {
				job.TranscriptJSONPath = &result.JSONPath
			}
			if result.MarkdownPath != "" {
				job.TranscriptMarkdownPath = &result.MarkdownPath
			}
		}
	}

	if err := h.jobRepo.Update(ctx, job); err != nil {
		// Rollback filesystem rename on DB failure.
		if bundleRenamed {
			_ = os.Rename(*job.ArtifactDir, originalArtifactDir)
			job.ArtifactDir = &originalArtifactDir
			job.AudioPath = originalAudioPath
			job.TranscriptJSONPath = originalJSONPath
			job.TranscriptMarkdownPath = originalMDPath
		}
		job.Title = originalTitle
		return err
	}

	h.ftsUpsertJob(job)
	return nil
}

func (h *Handler) refreshTitleSpeakersFromMappings(ctx context.Context, job *models.TranscriptionJob, mappings []models.SpeakerMapping) {
	if job == nil || job.Title == nil || strings.TrimSpace(*job.Title) == "" {
		return
	}

	rawTranscript := ""
	if job.Transcript != nil {
		rawTranscript = strings.TrimSpace(*job.Transcript)
	}
	if rawTranscript == "" && job.TranscriptJSONPath != nil && *job.TranscriptJSONPath != "" {
		if data, err := os.ReadFile(*job.TranscriptJSONPath); err == nil {
			rawTranscript = string(data)
		}
	}

	speakers := transcription.SpeakerTitleNamesFromTranscript(rawTranscript, mappings)
	nextTitle := transcription.WithSpeakerTitleBlock(*job.Title, speakers)
	if nextTitle == strings.TrimSpace(*job.Title) {
		return
	}

	if err := h.applyTranscriptionTitle(ctx, job, nextTitle); err != nil {
		logger.Warn("speaker title refresh failed", "job_id", job.ID, "error", err)
	}
}
