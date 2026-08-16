package api

import "testing"

func TestNormalizeGeneratedTranscriptTitle(t *testing.T) {
	got := normalizeGeneratedTranscriptTitle("  `Dreams\nand Wooden Walkways.`  ")
	want := "Dreams and Wooden Walkways"
	if got != want {
		t.Fatalf("normalizeGeneratedTranscriptTitle() = %q, want %q", got, want)
	}
}

func TestIsWeakGeneratedTranscriptTitleRejectsGenericTitles(t *testing.T) {
	for _, title := range []string{
		"Brief Note",
		"Personal Issue",
		"Conversation With Friend",
		"260809 09:47 Brief Note (dream) [Leona] {02:13}",
		"260705 23:42 20260705 234254-6385170C",
		"Thank You",
		"Rua Regedoura Guinta da Regedoura 77",
		"Title Not Available",
	} {
		if !isWeakGeneratedTranscriptTitle(title) {
			t.Fatalf("%q should be rejected as a weak generated title", title)
		}
	}
}

func TestIsWeakGeneratedTranscriptTitleAcceptsSpecificSubjectTitles(t *testing.T) {
	for _, title := range []string{
		"Dreams and Wooden Walkways",
		"Integrity, Self-Expression, and Trust",
		"Glosso Future Planning",
		"Healing Wound Care",
	} {
		if isWeakGeneratedTranscriptTitle(title) {
			t.Fatalf("%q should be accepted as a specific generated title", title)
		}
	}
}
