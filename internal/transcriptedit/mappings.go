package transcriptedit

import (
	"encoding/json"
	"reflect"
	"time"

	"gorm.io/gorm"
	"quill/internal/models"
)

func snapshotMappings(tx *gorm.DB, jobID string) (string, error) {
	// Legacy standalone editor tests/installations may have no mapping table.
	if !tx.Migrator().HasTable(&models.SpeakerMapping{}) {
		return "[]", nil
	}
	rows := []models.SpeakerMapping{}
	if err := tx.Where("transcription_job_id = ?", jobID).Order("id").Find(&rows).Error; err != nil {
		return "", err
	}
	raw, err := json.Marshal(rows)
	return string(raw), err
}

// A bundle sync can recreate rows in a different order with fresh IDs and
// timestamps. All speaker identity and review fields still guard acceptance.
func sameMappingEvidence(saved, current string) bool {
	if saved == current {
		return true
	}
	evidence := func(raw string) (map[string]models.SpeakerMapping, bool) {
		var rows []models.SpeakerMapping
		if err := json.Unmarshal([]byte(raw), &rows); err != nil || rows == nil {
			return nil, false
		}
		bySpeaker := make(map[string]models.SpeakerMapping, len(rows))
		for _, row := range rows {
			if _, duplicate := bySpeaker[row.OriginalSpeaker]; duplicate || row.OriginalSpeaker == "" {
				return nil, false
			}
			row.ID = 0
			row.CreatedAt = time.Time{}
			row.UpdatedAt = time.Time{}
			bySpeaker[row.OriginalSpeaker] = row
		}
		return bySpeaker, true
	}
	old, oldOK := evidence(saved)
	now, nowOK := evidence(current)
	return oldOK && nowOK && reflect.DeepEqual(old, now)
}

func replaceMappings(tx *gorm.DB, jobID, raw string) error {
	var rows []models.SpeakerMapping
	if err := json.Unmarshal([]byte(raw), &rows); err != nil {
		return err
	}
	if !tx.Migrator().HasTable(&models.SpeakerMapping{}) {
		if len(rows) == 0 {
			return nil
		}
		return ErrUnavailable
	}
	if err := tx.Where("transcription_job_id = ?", jobID).Delete(&models.SpeakerMapping{}).Error; err != nil {
		return err
	}
	for _, row := range rows {
		row.TranscriptionJobID = jobID
		if err := tx.Create(&row).Error; err != nil {
			return err
		}
	}
	return nil
}
