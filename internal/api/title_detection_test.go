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

func TestIsLikelyDefaultTranscriptionTitle_DetectsImportedAppleGenericTitle(t *testing.T) {
	title := "New Recording 261"
	job := &models.TranscriptionJob{
		ID:               "archive-6d98250694b2d88e9e53b8ceea961b6b",
		Title:            &title,
		AudioPath:        "/Users/leona/Documents/Voice Notes Archive/Recordings/20261004 194035-781A242A.m4a",
		OriginalFilename: "20261004 194035-781A242A.m4a",
	}

	if !isLikelyDefaultTranscriptionTitle(job) {
		t.Fatal("an imported Apple generic title should be eligible for auto-title")
	}
}

func TestIsLikelyDefaultTranscriptionTitle_DetectsImportedAppleLocationTitle(t *testing.T) {
	title := "Rua Regedoura Guinta da Regedoura 120 [Leona]"
	job := &models.TranscriptionJob{
		ID:               "archive-47ee7bb59fffb5704fc5a837e2384a5e",
		Title:            &title,
		AudioPath:        "/vault/Transcripts/recording/audio.m4a",
		OriginalFilename: "20261004 215448-74FB0B7B.m4a",
	}

	if !isLikelyDefaultTranscriptionTitle(job) {
		t.Fatal("an imported Apple location-only title should be eligible for auto-title")
	}
}

func TestIsLikelyDefaultTranscriptionTitle_PreservesCustomArchiveTitle(t *testing.T) {
	title := "abraham call, move forward work vs relationship [Leona, Abraham]"
	job := &models.TranscriptionJob{
		ID:               "archive-ed05",
		Title:            &title,
		AudioPath:        "/Users/leona/Documents/Voice Notes Archive/Recordings/20261005 162300.m4a",
		OriginalFilename: "20261005 162300.m4a",
	}

	if isLikelyDefaultTranscriptionTitle(job) {
		t.Fatal("a meaningful custom archive title should remain protected")
	}
}

func TestIsLikelyDefaultTranscriptionTitle_DetectsOriginalFilenameAfterBundling(t *testing.T) {
	title := "dream"
	job := &models.TranscriptionJob{
		Title:            &title,
		AudioPath:        "/vault/Transcripts/dream/audio.m4a",
		OriginalFilename: "dream.m4a",
	}

	if !isLikelyDefaultTranscriptionTitle(job) {
		t.Fatal("source filename title should remain eligible after audio is moved into a bundle")
	}
}
