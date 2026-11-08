package api

import (
	"encoding/json"
	"testing"
)

func TestLegacyPartialAttributionIsVisibleWithoutReplacingProvenance(t *testing.T) {
	var payload interface{}
	json.Unmarshal([]byte(`{"text":"Known and quiet","segments":[{"text":"Known","speaker":"a"},{"text":"quiet"}],"metadata":{"speaker_identity_status":"reference_verified"}}`), &payload)
	annotateLegacyTranscript(payload, true)
	metadata := payload.(map[string]interface{})["metadata"].(map[string]interface{})
	if metadata["diarization_status"] != "uncertain" || metadata["speaker_identity_status"] != "reference_verified" {
		t.Fatal(metadata)
	}
}
