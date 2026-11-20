package adapters

import (
	"encoding/json"
	"os"
	"path/filepath"
	"quill/internal/transcription/interfaces"
	"testing"
)

func TestReclusterJSONRetainsUnknownIntervals(t *testing.T) {
	var output speakerReclusterOutput
	if err := json.Unmarshal([]byte(`{"status":"uncertain","reason":"weak_voice_separation","segments":[{"start":0,"end":2,"speaker":"","confidence":0},{"start":3,"end":4,"speaker":"","confidence":0}]}`), &output); err != nil {
		t.Fatal(err)
	}
	result := &interfaces.DiarizationResult{}
	applySpeakerReclusterOutput(result, output)
	if len(result.Segments) != 2 || result.SpeakerCount != 0 || result.Segments[1].Start != 3 {
		t.Fatal("uncertain timeline was erased or exposed as people")
	}
}
func TestCommunityJSONRetainsRegularExclusiveAndProvenance(t *testing.T) {
	directory := t.TempDir()
	raw := `{"segments":[{"start":0,"end":2,"speaker":"a"}],"regular_segments":[{"start":0,"end":2,"speaker":"a"},{"start":1,"end":2,"speaker":"b"}],"speakers":["a","b"],"speaker_count":2,"metadata":{"diarization_device":"mps","confidence_kind":"unavailable"}}`
	if err := os.WriteFile(filepath.Join(directory, "result.json"), []byte(raw), 0600); err != nil {
		t.Fatal(err)
	}
	result, err := NewPyAnnoteAdapter(directory).parseJSONResult(directory)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.RegularSegments) != 2 || len(result.Segments) != 1 || result.Metadata["diarization_device"] != "mps" || result.Segments[0].Confidence != 0 {
		t.Fatal("lost overlap/provenance or invented confidence")
	}
}

func TestCommunityOverlapOnlyVoiceCannotBecomeSolo(t *testing.T) {
	directory := t.TempDir()
	raw := `{"segments":[{"start":0,"end":10,"speaker":"a"}],"regular_segments":[{"start":0,"end":10,"speaker":"a"},{"start":4,"end":4.4,"speaker":"b"}],"speakers":["a"],"speaker_count":1,"metadata":{"speaker_identity_status":"solo"}}`
	if err := os.WriteFile(filepath.Join(directory, "result.json"), []byte(raw), 0600); err != nil {
		t.Fatal(err)
	}
	result, err := NewPyAnnoteAdapter(directory).parseJSONResult(directory)
	if err != nil {
		t.Fatal(err)
	}
	if result.SpeakerCount != 2 || len(result.Speakers) != 2 || result.Metadata["speaker_identity_status"] == "solo" {
		t.Fatal("overlap-only voice disappeared", result)
	}
}
