package repository

import (
	"context"
	"testing"
	"time"

	"quill/internal/models"
)

func TestListWithParamsReviewWindowAndProjection(t *testing.T) {
	db := newJobTestDB(t)
	repo := NewJobRepository(db)
	ctx := context.Background()

	inside := time.Date(2026, 11, 5, 12, 0, 0, 0, time.UTC)
	outside := time.Date(2026, 10, 1, 12, 0, 0, 0, time.UTC)
	transcript := "large transcript content should not be selected"
	for _, job := range []models.TranscriptionJob{
		{ID: "inside", Status: models.StatusCompleted, RecordedAt: &inside, Transcript: &transcript},
		{ID: "outside", Status: models.StatusCompleted, RecordedAt: &outside, Transcript: &transcript},
	} {
		if err := repo.Create(ctx, &job); err != nil {
			t.Fatal(err)
		}
	}

	since := time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC)
	until := time.Date(2026, 11, 7, 0, 0, 0, 0, time.UTC)
	jobs, total, err := repo.ListWithParams(ctx, ListParams{
		Offset:         0,
		Limit:          10,
		RecordedAfter:  &since,
		RecordedBefore: &until,
		Status:         string(models.StatusCompleted),
		Columns:        []string{"id", "status", "recorded_at"},
	})
	if err != nil {
		t.Fatalf("ListWithParams: %v", err)
	}
	if total != 1 || len(jobs) != 1 || jobs[0].ID != "inside" {
		t.Fatalf("unexpected review window result: total=%d jobs=%v", total, jobs)
	}
	if jobs[0].Transcript != nil {
		t.Fatalf("projection unexpectedly loaded transcript")
	}
}
