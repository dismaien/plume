package main

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestVoiceComparisonIsBoundToTheInputAndProtectsReviewedWords(t *testing.T) {
	dir := t.TempDir()
	source, evidence := filepath.Join(dir, "source.json"), filepath.Join(dir, "changes.json")
	input := []byte(`{"word_segments":[{"word":"Yeah","start":1,"end":1.2,"speaker":"A","speaker_review":true}]}`)
	if err := os.WriteFile(source, input, 0600); err != nil {
		t.Fatal(err)
	}
	writeBatch := func(digest string, proposals []map[string]any) {
		t.Helper()
		data, err := json.Marshal(map[string]any{"input_sha256": digest, "proposals": proposals})
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(evidence, data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	sum := fmt.Sprintf("%x", sha256.Sum256(input))
	writeBatch(sum, []map[string]any{})
	if _, err := replayVoiceComparison(source, evidence); err != nil {
		t.Fatal(err)
	}
	writeBatch("different", []map[string]any{})
	if _, err := replayVoiceComparison(source, evidence); err == nil {
		t.Fatal("accepted stale evidence")
	}
	writeBatch(sum, []map[string]any{{"index": 0, "word": "Yeah", "previous": "A", "proposed": "B"}})
	if _, err := replayVoiceComparison(source, evidence); err == nil || err.Error() != "voice comparison requires an assigned machine word" {
		t.Fatalf("expected protection for reviewed word, got %v", err)
	}
}
