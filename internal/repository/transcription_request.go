package repository

import (
	"context"
	"fmt"
	"gorm.io/gorm"
	"quill/internal/models"
)

// TransitionTranscriptionRequest changes only processing settings/state, using
// a status CAS so simultaneous start requests cannot enqueue the same recording.
func (r *jobRepository) TransitionTranscriptionRequest(ctx context.Context, job *models.TranscriptionJob, expected models.JobStatus) error {
	statement := &gorm.Statement{DB: r.db}
	if err := statement.Parse(job); err != nil {
		return err
	}
	columns := []string{"status", "diarization", "error_message", "updated_at"}
	for _, field := range statement.Schema.Fields {
		if len(field.BindNames) > 1 && field.BindNames[0] == "Parameters" {
			columns = append(columns, field.DBName)
		}
	}
	result := r.db.WithContext(ctx).Model(&models.TranscriptionJob{}).Where("id = ? AND status = ?", job.ID, expected).Select(columns).Updates(job)
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return fmt.Errorf("recording state changed; reload before starting a rerun")
	}
	return nil
}
