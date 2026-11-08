// Package transcriptedit saves human corrections and immutable transcript history.
package transcriptedit

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"gorm.io/gorm"
	"quill/internal/models"
	"quill/internal/search"
)

var (
	ErrConflict    = errors.New("This transcript changed since you opened it. Reload the latest text before saving.")
	ErrUnavailable = errors.New("Only completed recordings with a transcript can be edited.")
	ErrInvalid     = errors.New("Invalid transcript edit.")
)

type Request struct {
	ExpectedVersion string          `json:"expected_version"`
	SegmentTexts    []string        `json:"segment_texts"`
	Text            *string         `json:"text"`
	RestoreID       *uint           `json:"restore_id"`
	SegmentParts    [][]SegmentPart `json:"segment_parts"`
}

// Each original section may be divided into independently attributed phrases.
type SegmentPart struct {
	Text    string `json:"text"`
	Speaker string `json:"speaker"`
}

type Document struct {
	Version    string          `json:"version"`
	Transcript json.RawMessage `json:"transcript"`
}

type Store struct{ DB *gorm.DB }

func token(raw string, revision uint) string {
	return fmt.Sprintf("%x-%d", sha256.Sum256([]byte(raw)), revision)
}

func latest(tx *gorm.DB, jobID string) (models.TranscriptRevision, error) {
	var r models.TranscriptRevision
	err := tx.Where("transcription_job_id = ?", jobID).Order("id DESC").Take(&r).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		err = nil
	}
	return r, err
}

func (s Store) Current(ctx context.Context, jobID string) (Document, error) {
	var doc Document
	err := s.DB.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		var job models.TranscriptionJob
		if err := tx.First(&job, "id = ?", jobID).Error; err != nil {
			return err
		}
		if job.Transcript == nil || !json.Valid([]byte(*job.Transcript)) {
			return ErrUnavailable
		}
		r, err := latest(tx, jobID)
		if err != nil {
			return err
		}
		doc = Document{token(*job.Transcript, r.ID), json.RawMessage(*job.Transcript)}
		return nil
	})
	return doc, err
}

func (s Store) History(ctx context.Context, jobID string, before uint) ([]models.TranscriptRevision, error) {
	var job models.TranscriptionJob
	if err := s.DB.WithContext(ctx).Select("id").First(&job, "id = ?", jobID).Error; err != nil {
		return nil, err
	}
	rows := []models.TranscriptRevision{}
	q := s.DB.WithContext(ctx).Omit("transcript", "base_transcript", "speaker_mappings").Where("transcription_job_id = ?", jobID)
	if before > 0 {
		q = q.Where("id < ?", before)
	}
	err := q.Order("id DESC").Limit(50).Find(&rows).Error
	return rows, err
}

func (s Store) Revision(ctx context.Context, jobID string, id uint) (models.TranscriptRevision, error) {
	var job models.TranscriptionJob
	if err := s.DB.WithContext(ctx).Select("id").First(&job, "id = ?", jobID).Error; err != nil {
		return models.TranscriptRevision{}, err
	}
	var r models.TranscriptRevision
	err := s.DB.WithContext(ctx).Where("transcription_job_id = ? AND id = ?", jobID, id).First(&r).Error
	return r, err
}

func (s Store) Save(ctx context.Context, jobID string, request Request) (Document, error) {
	var doc Document
	err := s.DB.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		var job models.TranscriptionJob
		if err := tx.First(&job, "id = ?", jobID).Error; err != nil {
			return err
		}
		if job.Status != models.StatusCompleted || job.Transcript == nil {
			return ErrUnavailable
		}
		previous := *job.Transcript
		last, err := latest(tx, jobID)
		if err != nil {
			return err
		}
		if request.ExpectedVersion == "" || request.ExpectedVersion != token(previous, last.ID) {
			return ErrConflict
		}
		mappingSnapshot, err := snapshotMappings(tx, jobID)
		if err != nil {
			return err
		}
		kind := "edit"
		var restoreMappings *string
		var restoreDiarization *bool
		var next string
		if request.RestoreID != nil {
			if request.Text != nil || request.SegmentTexts != nil || request.SegmentParts != nil {
				return ErrInvalid
			}
			var r models.TranscriptRevision
			if err := tx.Where("transcription_job_id = ? AND id = ?", jobID, *request.RestoreID).First(&r).Error; err != nil {
				return err
			}
			if r.Kind == "candidate" {
				if previous != r.BaseTranscript || r.SpeakerMappings == nil || *r.SpeakerMappings != mappingSnapshot {
					return ErrConflict
				}
				empty := "[]"
				restoreMappings = &empty
				kind = "candidate_accept"
			} else {
				restoreMappings = r.SpeakerMappings
				kind = "restore"
			}
			restoreDiarization = r.DiarizationRequested
			next = r.Transcript
		} else {
			if request.SegmentParts != nil {
				kind = "speaker_edit"
			}
			next, err = apply(previous, request)
			if err != nil {
				return err
			}
		}
		if next == previous && restoreMappings == nil {
			doc = Document{token(previous, last.ID), json.RawMessage(previous)}
			return nil
		}
		// Compare-and-swap also detects writers that do not participate in revisions.
		result := tx.Model(&models.TranscriptionJob{}).Where("id = ? AND transcript = ? AND status = ?", jobID, previous, models.StatusCompleted).Update("transcript", next)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return ErrConflict
		}
		// Capture the exact original, or a newer ASR result since the last manual edit.
		if last.ID == 0 || last.Transcript != previous || restoreMappings != nil {
			beforeKind := "before_edit"
			if last.ID == 0 {
				beforeKind = "original"
			}
			if err := tx.Create(&models.TranscriptRevision{TranscriptionJobID: jobID, Kind: beforeKind, Transcript: previous, SpeakerMappings: &mappingSnapshot, DiarizationRequested: &job.Diarization}).Error; err != nil {
				return err
			}
		}
		if restoreDiarization != nil {
			if err := tx.Model(&job).Update("diarization", *restoreDiarization).Error; err != nil {
				return err
			}
			job.Diarization = *restoreDiarization
		}
		if restoreMappings != nil {
			if err := replaceMappings(tx, jobID, *restoreMappings); err != nil {
				return err
			}
			if err := tx.Model(&job).Update("candidate_transcript_revision_id", nil).Error; err != nil {
				return err
			}
		}
		nextMappings, err := snapshotMappings(tx, jobID)
		if err != nil {
			return err
		}
		revision := models.TranscriptRevision{TranscriptionJobID: jobID, Kind: kind, Transcript: next, RestoredFromID: request.RestoreID, SpeakerMappings: &nextMappings, DiarizationRequested: &job.Diarization}
		if err := tx.Create(&revision).Error; err != nil {
			return err
		}

		// Keep search results consistent with the corrected text in this transaction.
		if tx.Migrator().HasTable("transcription_fts") {
			title, summary := "", ""
			if job.Title != nil {
				title = *job.Title
			}
			if job.Summary != nil {
				summary = *job.Summary
			}
			var payload struct {
				Text     string `json:"text"`
				Segments []struct {
					Text string `json:"text"`
				} `json:"segments"`
			}
			text := ""
			if json.Unmarshal([]byte(next), &payload) == nil {
				text = payload.Text
				if text == "" {
					parts := []string{}
					for _, seg := range payload.Segments {
						parts = append(parts, seg.Text)
					}
					text = strings.Join(parts, " ")
				}
			} else {
				_ = json.Unmarshal([]byte(next), &text)
			}
			if err := search.NewFTSManager(tx).Upsert(jobID, title, text, summary); err != nil {
				return err
			}
		}
		doc = Document{token(next, revision.ID), json.RawMessage(next)}
		return nil
	})
	return doc, err
}

func apply(raw string, request Request) (string, error) {
	if request.SegmentParts != nil {
		return applyParts(raw, request)
	}
	var p map[string]json.RawMessage
	if err := json.Unmarshal([]byte(raw), &p); err != nil || p == nil {
		// Legacy JSON-string transcripts have no segment structure.
		var plain string
		if strings.TrimSpace(raw) == "null" || json.Unmarshal([]byte(raw), &plain) != nil || request.Text == nil || request.SegmentTexts != nil {
			return "", ErrInvalid
		}
		if plain == *request.Text {
			return raw, nil
		}
		out, _ := json.Marshal(*request.Text)
		return string(out), nil
	}
	var segments []map[string]json.RawMessage
	if v := p["segments"]; len(v) > 0 && string(v) != "null" {
		if json.Unmarshal(v, &segments) != nil {
			return "", ErrInvalid
		}
	}
	changed := false
	if len(segments) > 0 {
		if request.Text != nil || len(request.SegmentTexts) != len(segments) {
			return "", ErrInvalid
		}
		for i, seg := range segments {
			if seg == nil {
				return "", ErrInvalid
			}
			var old string
			_ = json.Unmarshal(seg["text"], &old)
			if old != request.SegmentTexts[i] {
				changed = true
				seg["text"], _ = json.Marshal(request.SegmentTexts[i])
			}
		}
		if !changed {
			return raw, nil
		}
		// All word offsets become stale once text changes. Exact originals remain in history.
		for _, seg := range segments {
			delete(seg, "words")
			delete(seg, "tokens")
			delete(seg, "chars")
		}
		p["segments"], _ = json.Marshal(segments)
		p["text"], _ = json.Marshal(strings.Join(request.SegmentTexts, " "))
	} else {
		if request.Text == nil || request.SegmentTexts != nil {
			return "", ErrInvalid
		}
		var old string
		_ = json.Unmarshal(p["text"], &old)
		if old == *request.Text {
			return raw, nil
		}
		p["text"], _ = json.Marshal(*request.Text)
	}
	delete(p, "word_segments")
	p["manually_edited"] = json.RawMessage("true")
	out, err := json.Marshal(p)
	return string(out), err
}
