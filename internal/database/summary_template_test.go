package database

import (
	"testing"

	"quill/internal/models"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
)

func summaryTemplateTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open database: %v", err)
	}
	if err := db.AutoMigrate(&models.SummaryTemplate{}); err != nil {
		t.Fatalf("migrate database: %v", err)
	}
	return db
}

func TestSeedDefaultSummaryTemplateUpgradesUntouchedLegacyPrompt(t *testing.T) {
	db := summaryTemplateTestDB(t)
	legacy := models.SummaryTemplate{Name: "Meeting Notes", Model: "qwen2.5:7b", Prompt: legacyMeetingNotesPrompt}
	if err := db.Create(&legacy).Error; err != nil {
		t.Fatalf("create legacy template: %v", err)
	}

	if err := seedDefaultSummaryTemplate(db); err != nil {
		t.Fatalf("seed template: %v", err)
	}

	var got models.SummaryTemplate
	if err := db.First(&got, "id = ?", legacy.ID).Error; err != nil {
		t.Fatalf("read template: %v", err)
	}
	if got.Prompt != defaultMeetingNotesPrompt {
		t.Fatal("legacy prompt was not upgraded")
	}
	if !got.IncludeSpeakerInfo {
		t.Fatal("speaker information was not enabled")
	}
}

func TestSeedDefaultSummaryTemplateUpgradesPreviousStockPrompt(t *testing.T) {
	db := summaryTemplateTestDB(t)
	previous := models.SummaryTemplate{Name: "Meeting Notes", Model: "qwen2.5:7b", Prompt: previousDefaultMeetingNotesPrompt}
	if err := db.Create(&previous).Error; err != nil {
		t.Fatalf("create previous stock template: %v", err)
	}

	if err := seedDefaultSummaryTemplate(db); err != nil {
		t.Fatalf("seed template: %v", err)
	}

	var got models.SummaryTemplate
	if err := db.First(&got, "id = ?", previous.ID).Error; err != nil {
		t.Fatalf("read template: %v", err)
	}
	if got.Prompt != defaultMeetingNotesPrompt {
		t.Fatal("previous stock prompt was not upgraded")
	}
}

func TestSeedDefaultSummaryTemplatePreservesCustomizedPrompt(t *testing.T) {
	db := summaryTemplateTestDB(t)
	custom := models.SummaryTemplate{Name: "Meeting Notes", Model: "custom", Prompt: "My carefully customized prompt"}
	if err := db.Create(&custom).Error; err != nil {
		t.Fatalf("create custom template: %v", err)
	}

	if err := seedDefaultSummaryTemplate(db); err != nil {
		t.Fatalf("seed template: %v", err)
	}

	var got models.SummaryTemplate
	if err := db.First(&got, "id = ?", custom.ID).Error; err != nil {
		t.Fatalf("read template: %v", err)
	}
	if got.Prompt != custom.Prompt || got.Model != custom.Model {
		t.Fatalf("custom template was changed: %#v", got)
	}
}
