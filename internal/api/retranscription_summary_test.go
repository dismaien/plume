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
			if stored.Transcript == nil || *stored.Transcript != transcript || stored.Status != models.StatusPending {
				t.Fatal("retranscription was not queued with preserved transcript")
			}
		})
	}
}

func TestQueueRejectionPreservesAcceptedResultAndNames(t *testing.T) {
	h, db, _ := setupStartTranscriptionHarness(t)
	ctx := context.Background()
	text := `{"text":"human correction"}`
	summary := "manual summary"
	job := &models.TranscriptionJob{ID: "queue-reject", AudioPath: "source.wav", Status: models.StatusCompleted, Transcript: &text, Summary: &summary, Parameters: models.WhisperXParams{Model: "large-v3", Device: "cpu"}}
	if err := h.jobRepo.Create(ctx, job); err != nil {
		t.Fatal(err)
	}
	mapping := models.SpeakerMapping{TranscriptionJobID: job.ID, OriginalSpeaker: "speaker_0", CustomName: "Leona", MatchSource: "manual"}
	if err := db.Create(&mapping).Error; err != nil {
		t.Fatal(err)
	}
	h.taskQueue = queue.NewTaskQueue(1, nil, h.jobRepo)
	for i := 0; i < 200; i++ {
		if err := h.taskQueue.EnqueueJob("unused"); err != nil {
			t.Fatal(err)
		}
	}
	if err := h.startJob(ctx, job.ID, models.WhisperXParams{Model: "small"}); err == nil {
		t.Fatal("expected queue full")
	}
	stored, err := h.jobRepo.FindByID(ctx, job.ID)
	if err != nil {
		t.Fatal(err)
	}
	if stored.Status != models.StatusCompleted || stored.Transcript == nil || *stored.Transcript != text || stored.Parameters.Model != "large-v3" || stored.Summary == nil || *stored.Summary != summary {
		t.Fatal("enqueue rejection changed accepted work")
	}
	var maps []models.SpeakerMapping
	db.Find(&maps)
	if len(maps) != 1 || maps[0].CustomName != "Leona" {
		t.Fatal("enqueue rejection lost names")
	}
}

func TestAcceptedTranscriptAvailableDuringFailedRerun(t *testing.T) {
	h, _, router := setupStartTranscriptionHarness(t)
	ctx := context.Background()
	text := `{"text":"accepted text","segments":[{"start":0,"end":2,"text":"accepted text"}]}`
	job := &models.TranscriptionJob{ID: "failed-rerun", AudioPath: "source.wav", Status: models.StatusFailed, Transcript: &text, Diarization: true}
	if err := h.jobRepo.Create(ctx, job); err != nil {
		t.Fatal(err)
	}
	router.GET("/api/v1/transcription/:id/transcript", h.GetTranscript)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/transcription/failed-rerun/transcript", nil))
	if rec.Code != 200 || !bytes.Contains(rec.Body.Bytes(), []byte(`"available":true`)) || !bytes.Contains(rec.Body.Bytes(), []byte(`"diarization_status":"uncertain"`)) {
		t.Fatalf("accepted work hidden or missing-label outcome lost: %s", rec.Body.String())
	}
	stored, err := h.jobRepo.FindByID(ctx, job.ID)
	if err != nil {
		t.Fatal(err)
	}
	if *stored.Transcript != text {
		t.Fatal("read rewrote historical result")
	}
}
