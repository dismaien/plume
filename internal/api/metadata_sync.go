package api

import (
	"context"
	"os"

	"quill/internal/transcription"
	"quill/pkg/logger"
)

// syncMetadataToBundle writes/updates the metadata.json sidecar for a
// transcription job's bundle directory. This is called after any mutation
// (speaker mapping update, summary save, note CRUD, title change, folder move)
// so the on-disk metadata stays consistent with the database.
//
// Errors are logged but not returned — metadata sync is best-effort and
// must never break the primary mutation flow.
func (h *Handler) syncMetadataToBundle(ctx context.Context, jobID string) {
	job, err := h.jobRepo.FindByID(ctx, jobID)
	if err != nil {
		logger.Warn("metadata sync: job not found", "job_id", jobID, "error", err)
		return
	}

	if job.ArtifactDir == nil || *job.ArtifactDir == "" {
		return
	}

	mappings, _ := h.speakerMappingRepo.ListByJob(ctx, jobID)
	summaries, _ := h.summaryRepo.ListByTranscriptionID(ctx, jobID)
	notes, _ := h.noteRepo.ListByJob(ctx, jobID)

	meta := transcription.BuildMetadataFromJob(job, mappings, summaries, notes)
	metaPath := transcription.MetadataPath(*job.ArtifactDir)
	var syncService *transcription.BundleSyncService
	if h.bundleManager != nil {
		syncService = h.bundleManager.SyncService()
		if syncService != nil {
			syncService.BeginSelfWrite(metaPath)
		}
	}

	if err := transcription.WriteMetadata(*job.ArtifactDir, meta); err != nil {
		if syncService != nil {
			syncService.ClearSelfWrite(metaPath)
		}
		logger.Warn("metadata sync: failed to write metadata.json",
			"job_id", jobID, "dir", *job.ArtifactDir, "error", err)
		return
	}

	// Mark the file as self-written so the bundle watcher doesn't re-import it.
	if syncService != nil {
		if info, err := os.Stat(metaPath); err == nil {
			syncService.MarkSelfWrite(metaPath, info.ModTime().UnixNano())
		}
	}
}
