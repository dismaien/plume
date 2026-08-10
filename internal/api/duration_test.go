package api

import "testing"

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
