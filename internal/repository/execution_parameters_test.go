package repository

import (
	"context"
	"reflect"
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
	"quill/internal/models"
)

func TestCreateExecutionPreservesExplicitZeroParameters(t *testing.T) {
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&models.TranscriptionJobExecution{}); err != nil {
		t.Fatal(err)
	}
	repo := NewJobRepository(db)
	// Zero/false values in an execution are observations, not missing user defaults.
	params := models.WhisperXParams{ModelFamily: "whisper", Model: "large-v3", Device: "cpu", ComputeType: "int8", VadMethod: "silero"}
	execution := &models.TranscriptionJobExecution{TranscriptionJobID: "fixture", StartedAt: time.Now(), ActualParameters: params, Status: models.StatusProcessing}
	if err := repo.CreateExecution(context.Background(), execution); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(execution.ActualParameters, params) {
		t.Fatal("CreateExecution mutated explicit actual parameters with defaults")
	}
	var stored models.TranscriptionJobExecution
	if err := db.First(&stored, "id = ?", execution.ID).Error; err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(stored.ActualParameters, params) {
		t.Fatalf("stored parameters differ: got %+v, want %+v", stored.ActualParameters, params)
	}
	execution.Status = models.StatusCompleted
	if err := repo.UpdateExecution(context.Background(), execution); err != nil {
		t.Fatal(err)
	}
	if err := db.First(&stored, "id = ?", execution.ID).Error; err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(stored.ActualParameters, params) {
		t.Fatal("completion reintroduced defaults")
	}
}
