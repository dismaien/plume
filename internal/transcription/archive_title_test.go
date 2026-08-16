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
		Transcript:       "The last image in the dream is Omi holding up a ring.",
		Speakers:         []string{"Leona", "Leona"},
		DurationMillis:   237_973,
	})
	want := "260812 10:44 (dream) Sapphire Ring and Family [Leona] {03:57}"
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
		Transcript:       "The last image in the dream is Omi holding up a ring.",
		Speakers:         []string{"Leona"},
		DurationMillis:   237_973,
	})
	want := "260812 10:44 (dream) Family Travel and Sapphire Ring [Leona] {03:57}"
	if got != want {
		t.Fatalf("FormatGeneratedArchiveTitle() = %q, want %q", got, want)
	}
}

func TestFormatGeneratedArchiveTitleDetectsDreamFromTranscript(t *testing.T) {
	recordedAt := time.Date(2026, time.August, 9, 8, 13, 0, 0, time.UTC)
	got := FormatGeneratedArchiveTitle(GeneratedArchiveTitleInput{
		RecordedAt:       &recordedAt,
		Subject:          "Wooden Walkways and Hidden Rooms",
		OriginalFilename: "20260809 081311-EF53EB7E.qta",
		Transcript:       "I had a dream about walking through wooden rooms and hidden passages.",
		Speakers:         []string{"Leona"},
		DurationMillis:   352_000,
	})
	want := "260809 08:13 (dream) Wooden Walkways and Hidden Rooms [Leona] {05:52}"
	if got != want {
		t.Fatalf("FormatGeneratedArchiveTitle() = %q, want %q", got, want)
	}
}

func TestFormatGeneratedArchiveTitleDoesNotTagOrdinaryDreamPhrase(t *testing.T) {
	recordedAt := time.Date(2026, time.August, 9, 8, 13, 0, 0, time.UTC)
	got := FormatGeneratedArchiveTitle(GeneratedArchiveTitleInput{
		RecordedAt:       &recordedAt,
		Subject:          "Planning a Dream Home Renovation",
		OriginalFilename: "renovation.m4a",
		Transcript:       "We are planning the ideal renovation and discussing budgets.",
		DurationMillis:   352_000,
	})
	want := "260809 08:13 Planning a Dream Home Renovation (renovation) {05:52}"
	if got != want {
		t.Fatalf("FormatGeneratedArchiveTitle() = %q, want %q", got, want)
	}
}

func TestFormatGeneratedArchiveTitleDoesNotTagSongAboutDreams(t *testing.T) {
	recordedAt := time.Date(2026, time.December, 26, 21, 57, 0, 0, time.UTC)
	got := FormatGeneratedArchiveTitle(GeneratedArchiveTitleInput{
		RecordedAt:       &recordedAt,
		Subject:          "Dreams That Whisper Evening Song",
		OriginalFilename: "song.m4a",
		Transcript:       "Dreams that whisper in the evening, a song of light.",
		DurationMillis:   196_000,
	})
	want := "261226 21:57 Dreams That Whisper Evening Song (song) {03:16}"
	if got != want {
		t.Fatalf("FormatGeneratedArchiveTitle() = %q, want %q", got, want)
	}
}
