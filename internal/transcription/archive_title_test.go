package transcription

import (
	"testing"
	"time"
)

func TestFormatGeneratedArchiveTitlePreservesSourceLabel(t *testing.T) {
	recordedAt := time.Date(2026, time.August, 12, 10, 44, 56, 0, time.UTC)
	got := FormatGeneratedArchiveTitle(GeneratedArchiveTitleInput{
		RecordedAt:       &recordedAt,
		Subject:          "Sapphire Ring and Family Dream",
		CurrentTitle:     "dream",
		OriginalFilename: "dream.m4a",
		Speakers:         []string{"Leona", "Leona"},
		DurationMillis:   237_973,
	})
	want := "260812 10:44 Sapphire Ring and Family Dream (dream) [Leona] {03:57}"
	if got != want {
		t.Fatalf("FormatGeneratedArchiveTitle() = %q, want %q", got, want)
	}
}

func TestFormatGeneratedArchiveTitleOmitsMachineFilename(t *testing.T) {
	recordedAt := time.Date(2026, time.July, 24, 18, 36, 44, 0, time.UTC)
	got := FormatGeneratedArchiveTitle(GeneratedArchiveTitleInput{
		RecordedAt:       &recordedAt,
		Subject:          "Repairing the Garden Fence",
		OriginalFilename: "20260724 183644-60C99703.qta",
		DurationMillis:   5_072_000,
	})
	want := "260724 18:36 Repairing the Garden Fence {01:24}"
	if got != want {
		t.Fatalf("FormatGeneratedArchiveTitle() = %q, want %q", got, want)
	}
}

func TestFormatGeneratedArchiveTitlePreservesExplicitTitle(t *testing.T) {
	recordedAt := time.Date(2026, time.May, 11, 8, 0, 0, 0, time.UTC)
	got := FormatGeneratedArchiveTitle(GeneratedArchiveTitleInput{
		RecordedAt:       &recordedAt,
		Subject:          "Catching Up With Henri",
		CurrentTitle:     "morning with henri",
		OriginalFilename: "recording-2026-05-11.m4a",
		Speakers:         []string{"Leona", "Henri"},
		DurationMillis:   28_800_000,
	})
	want := "260511 08:00 Catching Up With Henri (morning with henri) [Leona, Henri] {08:00}"
	if got != want {
		t.Fatalf("FormatGeneratedArchiveTitle() = %q, want %q", got, want)
	}
}

func TestFormatGeneratedArchiveTitleDoesNotNestCanonicalTitle(t *testing.T) {
	recordedAt := time.Date(2026, time.August, 12, 10, 44, 56, 0, time.UTC)
	got := FormatGeneratedArchiveTitle(GeneratedArchiveTitleInput{
		RecordedAt:       &recordedAt,
		Subject:          "Family Travel and Sapphire Ring",
		CurrentTitle:     "260812 10:44 Sapphire Ring and Philadelphia Family Visit (dream) [Leona] {03:57}",
		OriginalFilename: "dream.m4a",
		Speakers:         []string{"Leona"},
		DurationMillis:   237_973,
	})
	want := "260812 10:44 Family Travel and Sapphire Ring (dream) [Leona] {03:57}"
	if got != want {
		t.Fatalf("FormatGeneratedArchiveTitle() = %q, want %q", got, want)
	}
}
