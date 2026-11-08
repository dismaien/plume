package api

import "strings"

// Older results lost stage metadata. Make provable missing output visible on
// read, without rewriting historical transcripts or inventing model provenance.
func annotateLegacyTranscript(payload interface{}, diarizationRequested bool) {
	p, ok := payload.(map[string]interface{})
	if !ok {
		return
	}
	metadata, _ := p["metadata"].(map[string]interface{})
	if metadata == nil {
		metadata = map[string]interface{}{}
		p["metadata"] = metadata
	}
	text, _ := p["text"].(string)
	hasText := strings.TrimSpace(text) != ""
	hasSpeaker := false
	missingSegment, missingWord, wordSpeaker := false, false, false
	for _, key := range []string{"segments", "word_segments"} {
		items, _ := p[key].([]interface{})
		for _, item := range items {
			row, _ := item.(map[string]interface{})
			if row == nil {
				continue
			}
			for _, textKey := range []string{"text", "word"} {
				value, _ := row[textKey].(string)
				hasText = hasText || strings.TrimSpace(value) != ""
			}
			speaker, _ := row["speaker"].(string)
			known := strings.TrimSpace(speaker) != ""
			hasSpeaker = hasSpeaker || known
			if key == "segments" {
				value, _ := row["text"].(string)
				missingSegment = missingSegment || strings.TrimSpace(value) != "" && !known
			}
			if key == "word_segments" {
				value, _ := row["word"].(string)
				missingWord = missingWord || strings.TrimSpace(value) != "" && !known
				wordSpeaker = wordSpeaker || known
			}
		}
	}
	if !hasText && metadata["text_status"] == nil {
		metadata["text_status"] = "needs_review"
		metadata["text_reason"] = "empty_output_not_verified_silence"
	}
	if diarizationRequested && (!hasSpeaker || missingSegment || wordSpeaker && missingWord) && metadata["diarization_status"] == nil {
		metadata["diarization_status"] = "uncertain"
		metadata["diarization_reason"] = "legacy_result_has_no_speaker_labels"
		if hasSpeaker {
			metadata["diarization_reason"] = "legacy_result_has_unattributed_turns"
		}
	}
}
