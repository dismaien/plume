package api

import (
	"testing"

	"quill/internal/models"
)

func TestIsLikelyDefaultTranscriptionTitle_DetectsAppleVoiceMemoFallback(t *testing.T) {
	title := "260705 23:42 20260705 234254-6385170C"
	job := &models.TranscriptionJob{Title: &title, AudioPath: "/archive/20260705 234254-6385170C.qta"}

	if !isLikelyDefaultTranscriptionTitle(job) {
		t.Fatal("Apple voice memo filename fallback should be eligible for auto-title")
	}
}

func TestIsLikelyDefaultTranscriptionTitle_PreservesLocationTitles(t *testing.T) {
	title := "260611 17:43 Schwarzwald-Baar Klinikum Villingen-Schwenningen"
	job := &models.TranscriptionJob{Title: &title, AudioPath: "/archive/20260611 174318-ABCD1234.qta"}

	if isLikelyDefaultTranscriptionTitle(job) {
		t.Fatal("human-readable Apple location titles should not be treated as filename fallbacks")
	}
}
