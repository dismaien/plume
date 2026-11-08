package transcriptedit

import (
	"encoding/json"
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
