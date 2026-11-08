package api

import (
	"fmt"
	"os"
	"testing"

	"quill/internal/models"
)

func TestPreferredRecordingDurationMillis_PrefersAudioMetadata(t *testing.T) {
	got := preferredRecordingDurationMillis(363008, 280306)
	if got != 363008 {
		t.Fatalf("preferredRecordingDurationMillis() = %d, want audio duration", got)
	}
}

func TestPreferredRecordingDurationMillis_FallsBackToTranscript(t *testing.T) {
	got := preferredRecordingDurationMillis(0, 280306)
	if got != 280306 {
		t.Fatalf("preferredRecordingDurationMillis() = %d, want transcript duration", got)
	}
}

func TestPopulateJobListComputedFieldsUsesPersistedSourceDurationWithoutAudioProbe(t *testing.T) {
	audio, err := os.CreateTemp(t.TempDir(), "recording-*.m4a")
	if err != nil {
		t.Fatal(err)
	}
	if err := audio.Close(); err != nil {
		t.Fatal(err)
	}
	transcript := fmt.Sprintf(`{"segments":[{"start":0,"end":%g,"text":"hello"}]}`, 1557.312)
	job := &models.TranscriptionJob{
		AudioPath:           audio.Name(),
		AudioDurationMillis: 6_657_705,
		Transcript:          &transcript,
	}

	populateJobListComputedFields(job)

	if !job.HasAudio {
		t.Fatal("expected existing source to be marked as audio")
	}
	if job.Duration != 6_657_705 {
		t.Fatalf("duration = %d, want persisted source duration 6657705", job.Duration)
	}
}

func TestPopulateJobListComputedFieldsFallsBackToTranscriptForLegacyRows(t *testing.T) {
	transcript := fmt.Sprintf(`{"segments":[{"start":0,"end":%g,"text":"hello"}]}`, 1557.312)
	job := &models.TranscriptionJob{Transcript: &transcript}

	populateJobListComputedFields(job)

	if job.Duration != 1_557_312 {
		t.Fatalf("duration = %d, want transcript fallback duration 1557312", job.Duration)
	}
}
