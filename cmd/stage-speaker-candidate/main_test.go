package main

import (
	"encoding/json"
	"strings"
	"testing"
)

const acceptedFixture = `{"text":"Hello there","language":"en","segments":[{"start":0,"end":2,"text":"Hello there","speaker":"speaker_04"}],"word_segments":[{"start":0,"end":0.9,"word":"Hello","score":0.8,"speaker":"speaker_04"},{"start":1.1,"end":2,"word":"there","score":0.7,"speaker":"speaker_04"}],"metadata":{"word_refinement_status":"applied_needs_review"}}`

const diarizationFixture = `{"model":"pyannote/speaker-diarization-community-1","speaker_count":2,"speakers":["speaker_00","speaker_01"],"segments":[{"start":0,"end":1,"speaker":"speaker_00"},{"start":1,"end":2,"speaker":"speaker_01"}],"regular_segments":[{"start":0,"end":1,"speaker":"speaker_00"},{"start":1,"end":2,"speaker":"speaker_01"}],"metadata":{"speaker_constraints":"{\"min_speakers\":2,\"max_speakers\":2}","diarization_device":"mps","mps_fallback_enabled":"0"}}`

func TestCandidatePreservesAcceptedWordsAndRequiresReview(t *testing.T) {
	raw, unknown, err := candidateTranscript(acceptedFixture, []byte(diarizationFixture))
	if err != nil {
		t.Fatal(err)
	}
	if unknown != 0 {
		t.Fatalf("unattributed words = %d, want 0", unknown)
	}
	var candidate struct {
		Text  string `json:"text"`
		Words []struct {
			Start   float64 `json:"start"`
			End     float64 `json:"end"`
			Word    string  `json:"word"`
			Speaker string  `json:"speaker"`
		} `json:"word_segments"`
		Metadata map[string]string `json:"metadata"`
	}
	if err := json.Unmarshal([]byte(raw), &candidate); err != nil {
		t.Fatal(err)
	}
	if candidate.Text != "Hello there" || len(candidate.Words) != 2 ||
		candidate.Words[0].Start != 0 || candidate.Words[0].End != 0.9 || candidate.Words[0].Speaker != "speaker_00" ||
		candidate.Words[1].Start != 1.1 || candidate.Words[1].End != 2 || candidate.Words[1].Speaker != "speaker_01" {
		t.Fatalf("candidate changed words or speaker assignment: %+v", candidate.Words)
	}
	if candidate.Metadata["diarization_status"] != "uncertain" || candidate.Metadata["word_refinement_status"] != "" {
		t.Fatalf("candidate must require review and not claim prior refinement: %+v", candidate.Metadata)
	}
}

func TestCandidateRejectsUnconstrainedOrFallbackDiarization(t *testing.T) {
	for _, raw := range []string{
		strings.Replace(diarizationFixture, `\"max_speakers\":2`, `\"max_speakers\":4`, 1),
		strings.Replace(diarizationFixture, `"mps_fallback_enabled":"0"`, `"mps_fallback_enabled":"1"`, 1),
		strings.Replace(diarizationFixture, `"speaker_count":2`, `"speaker_count":3`, 1),
	} {
		if _, _, err := candidateTranscript(acceptedFixture, []byte(raw)); err == nil {
			t.Fatal("unsafe diarization was accepted")
		}
	}
}
