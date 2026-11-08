package transcriptedit

import (
	"encoding/json"
	"math"
	"strings"
	"unicode/utf8"
)

func applyParts(raw string, request Request) (string, error) {
	if request.Text != nil || request.SegmentTexts != nil {
		return "", ErrInvalid
	}
	var payload map[string]json.RawMessage
	if json.Unmarshal([]byte(raw), &payload) != nil || payload == nil {
		return "", ErrInvalid
	}
	var segments []map[string]json.RawMessage
	if json.Unmarshal(payload["segments"], &segments) != nil || len(segments) == 0 || len(segments) != len(request.SegmentParts) {
		return "", ErrInvalid
	}
	speakers := map[string]json.RawMessage{"": nil}
	for _, seg := range segments {
		if seg == nil {
			return "", ErrInvalid
		}
		var speaker string
		_ = json.Unmarshal(seg["speaker"], &speaker)
		speakers[speaker] = seg["speaker_name"]
	}
	changed := false
	result := []map[string]json.RawMessage{}
	texts := []string{}
	for i, seg := range segments {
		parts := request.SegmentParts[i]
		if len(parts) == 0 || len(parts) > 100 {
			return "", ErrInvalid
		}
		var oldText, oldSpeaker string
		_ = json.Unmarshal(seg["text"], &oldText)
		_ = json.Unmarshal(seg["speaker"], &oldSpeaker)
		if len(parts) != 1 || parts[0].Text != oldText || parts[0].Speaker != oldSpeaker {
			changed = true
		}
		total := 0
		for _, part := range parts {
			if _, ok := speakers[part.Speaker]; !ok {
				return "", ErrInvalid
			}
			if len(parts) > 1 && strings.TrimSpace(part.Text) == "" {
				return "", ErrInvalid
			}
			total += utf8.RuneCountInString(part.Text)
		}
		var start, end float64
		if len(parts) > 1 {
			if json.Unmarshal(seg["start"], &start) != nil || json.Unmarshal(seg["end"], &end) != nil || math.IsNaN(start+end) || math.IsInf(start+end, 0) || end < start || total == 0 {
				return "", ErrInvalid
			}
		}
		offset := 0
		sectionText := ""
		for _, part := range parts {
			clone := map[string]json.RawMessage{}
			for key, value := range seg {
				clone[key] = value
			}
			clone["text"], _ = json.Marshal(part.Text)
			if part.Speaker != oldSpeaker {
				clone["speaker"], _ = json.Marshal(part.Speaker)
				delete(clone, "speaker_name")
				if name := speakers[part.Speaker]; len(name) > 0 {
					clone["speaker_name"] = name
				}
			}
			if len(parts) > 1 {
				// Text selection has no reliable forced alignment after user corrections.
				// Preserve the section bounds, and explicitly mark interpolated seek times.
				clone["start"], _ = json.Marshal(start + (end-start)*float64(offset)/float64(total))
				offset += utf8.RuneCountInString(part.Text)
				clone["end"], _ = json.Marshal(start + (end-start)*float64(offset)/float64(total))
				clone["timing_approximate"] = json.RawMessage("true")
			}
			result = append(result, clone)
			sectionText += part.Text
		}
		texts = append(texts, sectionText)
	}
	if !changed {
		return raw, nil
	}
	for _, seg := range result {
		delete(seg, "words")
		delete(seg, "tokens")
		delete(seg, "chars")
	}
	payload["segments"], _ = json.Marshal(result)
	payload["text"], _ = json.Marshal(strings.Join(texts, " "))
	delete(payload, "word_segments")
	payload["manually_edited"] = json.RawMessage("true")
	out, err := json.Marshal(payload)
	return string(out), err
}
