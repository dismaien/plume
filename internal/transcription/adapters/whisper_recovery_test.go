package adapters

import "testing"

func TestASRRecoveryPreservesDecodedTextAndTimings(t *testing.T) {
	result, err := recoverFullAudioTranscript([]byte(`{"decoder":"faster_whisper_full_audio","language":"de","segments":[{"start":1.2,"end":3.4,"text":" Leise Stimme.","words":[{"start":1.2,"end":2,"word":" Leise","probability":0.7}]}]}`))
	if err != nil {
		t.Fatal(err)
	}
	if result.Text != " Leise Stimme." || result.Language != "de" || result.WordSegments[0].Start != 1.2 || result.Metadata["alignment_status"] != "failed" {
		t.Fatalf("bad recovered result: %+v", result)
	}
	if _, err := recoverFullAudioTranscript([]byte(`{"segments":[]}`)); err == nil {
		t.Fatal("accepted incomplete recovery evidence")
	}
}
