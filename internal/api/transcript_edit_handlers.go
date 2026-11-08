package api

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
	"quill/internal/database"
	"quill/internal/transcriptedit"
	"quill/internal/transcription"
)

func transcriptEditError(c *gin.Context, err error) {
	status := http.StatusInternalServerError
	message := "Could not save or load transcript history. Your text has not been discarded; try again."
	switch {
	case errors.Is(err, gorm.ErrRecordNotFound):
		status = http.StatusNotFound
		message = "Recording or revision not found."
	case errors.Is(err, transcriptedit.ErrConflict):
		status = http.StatusConflict
		message = err.Error()
	case errors.Is(err, transcriptedit.ErrUnavailable):
		status = http.StatusConflict
		message = err.Error()
	case errors.Is(err, transcriptedit.ErrInvalid):
		status = http.StatusBadRequest
		message = err.Error()
	}
	c.JSON(status, gin.H{"error": message})
}

func (h *Handler) GetTranscriptEditor(c *gin.Context) {
	doc, err := (transcriptedit.Store{DB: database.DB}).Current(c.Request.Context(), c.Param("id"))
	if err != nil {
		transcriptEditError(c, err)
		return
	}
	c.JSON(http.StatusOK, doc)
}

func (h *Handler) SaveTranscriptEdit(c *gin.Context) {
	// Bound text input without limiting the number of revisions in the archive.
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 8<<20)
	var request transcriptedit.Request
	if c.ShouldBindJSON(&request) != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid edit or text exceeds 8 MB."})
		return
	}
	doc, err := (transcriptedit.Store{DB: database.DB}).Save(c.Request.Context(), c.Param("id"), request)
	if err != nil {
		transcriptEditError(c, err)
		return
	}
	// DB/history are authoritative; regenerate derived files after every save,
	// including an idempotent retry following a filesystem failure.
	warning := ""
	if job, loadErr := h.jobRepo.FindByID(c.Request.Context(), c.Param("id")); loadErr == nil {
		if h.bundleManager != nil && job.ArtifactDir != nil {
			h.bundleManager.SyncService().BeginSelfWrite(transcription.MetadataPath(*job.ArtifactDir))
		}
		if fileErr := transcription.RefreshAcceptedTranscriptFiles(job); fileErr != nil {
			warning = "Transcript saved in history, but export files could not be refreshed. Retry saving to refresh them."
		}
		if h.speakerMappingRepo != nil && h.summaryRepo != nil && h.noteRepo != nil {
			h.syncMetadataToBundle(c.Request.Context(), job.ID)
		}
	}
	c.JSON(http.StatusOK, gin.H{"version": doc.Version, "transcript": doc.Transcript, "artifact_warning": warning})
}

func (h *Handler) ListTranscriptRevisions(c *gin.Context) {
	before, err := strconv.ParseUint(c.DefaultQuery("before", "0"), 10, 64)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid history page."})
		return
	}
	rows, err := (transcriptedit.Store{DB: database.DB}).History(c.Request.Context(), c.Param("id"), uint(before))
	if err != nil {
		transcriptEditError(c, err)
		return
	}
	var next *uint
	if len(rows) == 50 {
		n := rows[len(rows)-1].ID
		next = &n
	}
	c.JSON(http.StatusOK, gin.H{"revisions": rows, "next_before": next})
}

func (h *Handler) GetTranscriptRevision(c *gin.Context) {
	id, err := strconv.ParseUint(c.Param("revisionId"), 10, 64)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid revision."})
		return
	}
	row, err := (transcriptedit.Store{DB: database.DB}).Revision(c.Request.Context(), c.Param("id"), uint(id))
	if err != nil {
		transcriptEditError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"revision": row, "transcript": json.RawMessage(row.Transcript)})
}
