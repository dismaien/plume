package models

import "time"

// TranscriptRevision is an immutable snapshot. It intentionally has no cascading
// job relation: retranscription and recording soft deletion must retain history.
type TranscriptRevision struct {
	ID                   uint      `json:"id" gorm:"primaryKey"`
	TranscriptionJobID   string    `json:"job_id" gorm:"not null;index:idx_transcript_revision_job_id,priority:1"`
	Kind                 string    `json:"kind" gorm:"not null"`
	Transcript           string    `json:"-" gorm:"type:text;not null"`
	DiarizationRequested *bool     `json:"diarization_requested,omitempty"`
	BaseTranscript       string    `json:"-" gorm:"type:text"`
	SpeakerMappings      *string   `json:"-" gorm:"type:text"`
	RestoredFromID       *uint     `json:"restored_from_id,omitempty"`
	CreatedAt            time.Time `json:"created_at" gorm:"autoCreateTime;index:idx_transcript_revision_job_id,priority:2"`
}
