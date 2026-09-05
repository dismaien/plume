package api

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"quill/internal/models"
	"quill/internal/queue"
)

func TestRetranscriptionPreservesExistingSummary(t *testing.T) {
	for _, batch := range []bool{false, true} {
		name := "single"
		if batch {
			name = "batch"
		}
		t.Run(name, func(t *testing.T) {
			h, _, router := setupStartTranscriptionHarness(t)
			summary := "User-edited summary that must remain available"
			transcript := `{"segments":[{"text":"original speech"}]}`
			job := &models.TranscriptionJob{ID: "summary-fixture", AudioPath: "/tmp/fixture.wav", Status: models.StatusCompleted, Summary: &summary, Transcript: &transcript}
			if err := h.jobRepo.Create(context.Background(), job); err != nil {
				t.Fatal(err)
			}
			if batch {
				if err := h.startJob(context.Background(), job.ID, models.WhisperXParams{ModelFamily: "whisper", Model: "large-v3"}); err != nil {
					t.Fatal(err)
				}
			} else {
				h.taskQueue = queue.NewTaskQueue(1, nil, h.jobRepo)
				rec := httptest.NewRecorder()
				req := httptest.NewRequest(http.MethodPost, "/api/v1/transcription/"+job.ID+"/start", bytes.NewBufferString(`{"model_family":"whisper","model":"large-v3"}`))
				req.Header.Set("Content-Type", "application/json")
				router.ServeHTTP(rec, req)
				if rec.Code != http.StatusOK {
					t.Fatalf("start: %d %s", rec.Code, rec.Body.String())
				}
			}
			stored, err := h.jobRepo.FindByID(context.Background(), job.ID)
			if err != nil {
				t.Fatal(err)
			}
			if stored.Summary == nil || *stored.Summary != summary {
				t.Fatal("retranscription discarded existing summary")
			}
			if stored.Transcript != nil || stored.Status != models.StatusPending {
				t.Fatal("retranscription was not queued with cleared transcript")
			}
		})
	}
}
