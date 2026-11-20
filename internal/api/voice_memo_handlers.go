package api

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"

	"quill/internal/voicememos"

	"github.com/gin-gonic/gin"
)

// SyncVoiceMemos archives, imports, and queues Voice Memos that Plume has not seen yet.
func (h *Handler) SyncVoiceMemos(c *gin.Context) {
	if h.voiceMemoSyncService == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Voice Memos sync is not configured"})
		return
	}

	ctx, cancel := context.WithTimeout(c.Request.Context(), 20*time.Minute)
	defer cancel()
	apiBase := fmt.Sprintf("http://127.0.0.1:%s/api/v1", h.config.Port)
	result, err := h.voiceMemoSyncService.SyncAndStart(ctx, apiBase)
	if err != nil {
		status := http.StatusInternalServerError
		if errors.Is(err, voicememos.ErrSyncInProgress) {
			status = http.StatusConflict
		}
		c.JSON(status, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, result)
}
