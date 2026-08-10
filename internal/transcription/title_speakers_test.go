package transcription

import (
	"testing"

	"quill/internal/models"
)

func TestWithSpeakerTitleBlock_ReplacesSuffixSpeakerBlock(t *testing.T) {
	title := "260706 13:53 Is This Safe [Duncan] @Rua Regedoura Guindais {00:43}"
	got := WithSpeakerTitleBlock(title, []string{"Ivan Vendrov"})
	want := "260706 13:53 Is This Safe [Ivan] @Rua Regedoura Guindais {00:43}"
	if got != want {
		t.Fatalf("WithSpeakerTitleBlock() = %q, want %q", got, want)
	}
}

func TestWithSpeakerTitleBlock_InsertsBeforeLocationAndDuration(t *testing.T) {
	title := "260706 13:53 Is This Safe @Rua Regedoura Guindais {00:43}"
	got := WithSpeakerTitleBlock(title, []string{"Leona", "Ivan Vendrov"})
	want := "260706 13:53 Is This Safe [Leona, Ivan] @Rua Regedoura Guindais {00:43}"
	if got != want {
		t.Fatalf("WithSpeakerTitleBlock() = %q, want %q", got, want)
	}
}

func TestWithSpeakerTitleBlock_InsertsBeforeDuration(t *testing.T) {
	title := "260321 21:40 Healing Wound Care (krankenhaus 2) {20:08}"
	got := WithSpeakerTitleBlock(title, []string{"Leona"})
	want := "260321 21:40 Healing Wound Care (krankenhaus 2) [Leona] {20:08}"
	if got != want {
		t.Fatalf("WithSpeakerTitleBlock() = %q, want %q", got, want)
	}
}

func TestSpeakerTitleNamesFromTranscript_UsesMappingOverStaleSpeakerName(t *testing.T) {
	raw := `{
		"segments": [
			{"start": 0, "speaker": "speaker_00", "speaker_name": "Duncan", "text": "old"},
			{"start": 5, "speaker": "speaker_01", "speaker_name": "Speaker B", "text": "raw"}
		]
	}`
	mappings := []models.SpeakerMapping{
		{OriginalSpeaker: "speaker_00", CustomName: "Ivan Vendrov"},
		{OriginalSpeaker: "speaker_01", CustomName: "Leona"},
	}
	got := SpeakerTitleNamesFromTranscript(raw, mappings)
	want := []string{"Ivan", "Leona"}
	if len(got) != len(want) {
		t.Fatalf("len = %d, want %d (%v)", len(got), len(want), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("name[%d] = %q, want %q (all: %v)", i, got[i], want[i], got)
		}
	}
}

func TestSpeakerTitleName_DropsGenericLabels(t *testing.T) {
	for _, input := range []string{"", "Unknown", "Speaker A", "speaker_00"} {
		if got := SpeakerTitleName(input); got != "" {
			t.Fatalf("SpeakerTitleName(%q) = %q, want empty", input, got)
		}
	}
}
