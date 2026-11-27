package transcriptedit

import (
	"encoding/json"
	"math"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"unicode"
)

type PassageReview struct {
	WordingOnly bool   `json:"wording_only,omitempty"`
	Segment     int    `json:"segment"`
	FirstWord   *int   `json:"first_word"`
	LastWord    *int   `json:"last_word"`
	Original    string `json:"original"`
	Text        string `json:"text"`
	Speaker     string `json:"speaker"`
	Cue         bool   `json:"cue"`
}

type reviewItem = map[string]any

var reviewTokens = regexp.MustCompile(`\S+`)

func valueText(node reviewItem, key string) string { v, _ := node[key].(string); return v }
func copied(node reviewItem) reviewItem {
	m := reviewItem{}
	for k, v := range node {
		m[k] = v
	}
	return m
}
func reviewed(node reviewItem, speaker string) {
	if valueText(node, "speaker") != speaker {
		delete(node, "speaker_name")
	}
	if speaker == "" {
		delete(node, "speaker")
		delete(node, "speaker_name")
	} else {
		node["speaker"] = speaker
	}
	delete(node, "speaker_review")
	node["speaker_reviewed"] = true
}
func unpackItems(field json.RawMessage) ([]reviewItem, error) {
	var out []reviewItem
	if len(field) > 0 && json.Unmarshal(field, &out) != nil {
		return nil, ErrInvalid
	}
	for _, item := range out {
		if item == nil {
			return nil, ErrInvalid
		}
	}
	return out, nil
}

// Match at the selected token offset. Searching elsewhere could overwrite another
// occurrence of the same reply in a manually edited transcript.
func reviewSpan(section, original string, offset int) (int, int, bool) {
	spans := reviewTokens.FindAllStringIndex(section, -1)
	wanted := strings.Fields(original)
	if len(wanted) == 0 || offset < 0 || offset+len(wanted) > len(spans) {
		return 0, 0, false
	}
	for i, token := range wanted {
		span := spans[offset+i]
		if section[span[0]:span[1]] != token {
			return 0, 0, false
		}
	}
	return spans[offset][0], spans[offset+len(wanted)-1][1], true
}

// Use the same midpoint boundaries as the review list. Overlapping segments
// can contain identical replies at the same displayed second.
func passageWordOffset(segs, words []reviewItem, segment, first, last int) (int, bool) {
	current, offset := 0, 0
	for i, word := range words[:last+1] {
		start, hasStart := word["start"].(float64)
		end, hasEnd := word["end"].(float64)
		if hasStart && hasEnd {
			midpoint := (start + end) / 2
			for current+1 < len(segs) {
				sectionEnd, _ := segs[current]["end"].(float64)
				nextStart, _ := segs[current+1]["start"].(float64)
				if midpoint < math.Max(sectionEnd, nextStart) {
					break
				}
				current++
			}
		}
		if i >= first && current != segment {
			return 0, false
		}
		if current == segment && i < first {
			offset += len(strings.Fields(valueText(word, "word")))
		}
	}
	return offset, true
}

func applyPassage(raw string, r PassageReview) (string, error) {
	var doc map[string]json.RawMessage
	if json.Unmarshal([]byte(raw), &doc) != nil || doc == nil || strings.TrimSpace(r.Text) == "" || len(r.Speaker) > 255 || strings.IndexFunc(r.Speaker, unicode.IsControl) >= 0 {
		return "", ErrInvalid
	}
	segs, err := unpackItems(doc["segments"])
	if err != nil {
		return "", err
	}
	words, err := unpackItems(doc["word_segments"])
	if err != nil {
		return "", err
	}
	if r.Segment < -1 || r.Segment >= len(segs) || (r.Segment == -1 && len(segs) > 0) {
		return "", ErrInvalid
	}
	speaker := strings.TrimSpace(r.Speaker)
	text := strings.TrimSpace(r.Text)
	original := strings.TrimSpace(r.Original)
	var seg reviewItem
	if r.Segment >= 0 {
		seg = segs[r.Segment]
	}
	var fullOffset int
	if seg != nil {
		for _, other := range segs[:r.Segment] {
			fullOffset += len(strings.Fields(valueText(other, "text")))
		}
	}
	var start, end float64
	if r.FirstWord == nil || r.LastWord == nil {
		if len(words) > 0 || seg == nil || r.FirstWord != nil || r.LastWord != nil || strings.TrimSpace(valueText(seg, "text")) != original {
			return "", ErrInvalid
		}
		if !r.WordingOnly {
			reviewed(seg, speaker)
		}
		if original != text {
			seg["text"] = text
			clearAlignment(seg)
		}
	} else {
		first, last := *r.FirstWord, *r.LastWord
		if first < 0 || last < first || last >= len(words) {
			return "", ErrInvalid
		}
		pieces := []string{}
		for _, w := range words[first : last+1] {
			pieces = append(pieces, strings.TrimSpace(valueText(w, "word")))
		}
		if strings.Join(pieces, " ") != original {
			return "", ErrConflict
		}
		var startOK, endOK bool
		start, startOK = words[first]["start"].(float64)
		end, endOK = words[last]["end"].(float64)
		if !startOK || !endOK || start < 0 || end < start {
			return "", ErrInvalid
		}
		if seg != nil {
			ss, _ := seg["start"].(float64)
			section := valueText(seg, "text")
			offset, ok := passageWordOffset(segs, words, r.Segment, first, last)
			if !ok {
				return "", ErrConflict
			}
			lo, hi, ok := reviewSpan(section, original, offset)
			if !ok {
				return "", ErrConflict
			}
			before, after := section[:lo], section[hi:]
			fullOffset += len(strings.Fields(before))
			sectionEnd, _ := seg["end"].(float64)
			if sectionEnd < ss || end < ss || start > sectionEnd {
				return "", ErrInvalid
			}
			splitStart, splitEnd := math.Max(start, ss), math.Min(end, sectionEnd)
			if r.WordingOnly {
				seg["text"] = before + text + after
				clearAlignment(seg)
			} else {
				parts := []reviewItem{}
				center := copied(seg)
				clearAlignment(center)
				reviewed(center, speaker)
				if strings.TrimSpace(before) != "" {
					left := copied(seg)
					clearAlignment(left)
					left["text"] = before
					left["end"] = splitStart
					parts = append(parts, left)
					center["start"] = splitStart
					before = ""
				}
				if strings.TrimSpace(after) != "" {
					center["end"] = splitEnd
				}
				center["text"] = before + text
				if strings.TrimSpace(after) == "" {
					center["text"] = before + text + after
				}
				parts = append(parts, center)
				if strings.TrimSpace(after) != "" {
					right := copied(seg)
					clearAlignment(right)
					right["text"] = after
					right["start"] = splitEnd
					parts = append(parts, right)
				}
				segs = slices.Replace(segs, r.Segment, r.Segment+1, parts...)
			}
		}
		if r.WordingOnly {
			if original == text {
				return raw, nil
			}
			updated, err := replaceWording(words[first:last+1], strings.Fields(text))
			if err != nil {
				return "", err
			}
			words = slices.Replace(words, first, last+1, updated...)
		} else if original == text {
			for _, w := range words[first : last+1] {
				reviewed(w, speaker)
			}
		} else {
			tokens := strings.Fields(text)
			replacement := make([]reviewItem, len(tokens))
			for i, t := range tokens {
				w := reviewItem{"word": t, "start": start + (end-start)*float64(i)/float64(len(tokens)), "end": start + (end-start)*float64(i+1)/float64(len(tokens)), "timing_approximate": true}
				if len(tokens) == last-first+1 {
					w = copied(words[first+i])
					w["word"] = t
					if t != strings.TrimSpace(valueText(words[first+i], "word")) {
						delete(w, "score")
					}
				}
				reviewed(w, speaker)
				replacement[i] = w
			}
			words = slices.Replace(words, first, last+1, replacement...)
		}
		doc["word_segments"], _ = json.Marshal(words)
	}
	if len(segs) > 0 {
		doc["segments"], _ = json.Marshal(segs)
	}
	if original != text {
		if seg == nil && r.FirstWord != nil {
			for _, w := range words[:*r.FirstWord] {
				fullOffset += len(strings.Fields(valueText(w, "word")))
			}
		}
		var full string
		if json.Unmarshal(doc["text"], &full) == nil && full != "" {
			lo, hi, ok := reviewSpan(full, original, fullOffset)
			if !ok {
				return "", ErrConflict
			}
			full = full[:lo] + text + full[hi:]
		} else {
			var blocks []string
			for _, s := range segs {
				blocks = append(blocks, strings.TrimSpace(valueText(s, "text")))
			}
			if len(segs) == 0 {
				for _, w := range words {
					blocks = append(blocks, valueText(w, "word"))
				}
			}
			full = strings.Join(blocks, " ")
		}
		doc["text"], _ = json.Marshal(full)
	}
	if r.Cue && !r.WordingOnly {
		var m map[string]string
		if json.Unmarshal(doc["metadata"], &m) == nil && m != nil {
			delete(m, "speaker_review_start")
			delete(m, "speaker_review_reason")
			doc["metadata"], _ = json.Marshal(m)
		}
	}
	doc["manually_edited"] = json.RawMessage("true")
	result, err := json.Marshal(doc)
	return string(result), err
}

func clearAlignment(s reviewItem) {
	for _, k := range []string{"words", "tokens", "chars"} {
		delete(s, k)
	}
}

// Retain the exact timing and attribution of matching words on either side of a
// correction. Inserted words inherit the selected turn, never a nearby reply.
func replaceWording(words []reviewItem, tokens []string) ([]reviewItem, error) {
	first := words[0]
	for _, w := range words {
		for _, key := range []string{"speaker", "speaker_name", "speaker_review", "speaker_reviewed"} {
			if !reflect.DeepEqual(w[key], first[key]) {
				return nil, ErrConflict
			}
		}
	}
	left, right := 0, 0
	for left < len(words) && left < len(tokens) && strings.TrimSpace(valueText(words[left], "word")) == tokens[left] {
		left++
	}
	for right < len(words)-left && right < len(tokens)-left &&
		strings.TrimSpace(valueText(words[len(words)-1-right], "word")) == tokens[len(tokens)-1-right] {
		right++
	}
	old := words[left : len(words)-right]
	middle := tokens[left : len(tokens)-right]
	start, _ := first["start"].(float64)
	end, _ := words[len(words)-1]["end"].(float64)
	if left > 0 {
		start, _ = words[left-1]["end"].(float64)
	}
	if right > 0 {
		end, _ = words[len(words)-right]["start"].(float64)
	}
	if len(old) > 0 {
		start, _ = old[0]["start"].(float64)
		end, _ = old[len(old)-1]["end"].(float64)
	}
	if end < start {
		end = start
	}
	out := slices.Clone(words[:left])
	for i, token := range middle {
		var w reviewItem
		if len(old) == len(middle) {
			w = copied(old[i])
			delete(w, "score")
		} else {
			w = reviewItem{"start": start + (end-start)*float64(i)/float64(len(middle)), "end": start + (end-start)*float64(i+1)/float64(len(middle)), "timing_approximate": true}
			for _, key := range []string{"speaker", "speaker_name", "speaker_review", "speaker_reviewed"} {
				if v, ok := first[key]; ok {
					w[key] = v
				}
			}
		}
		w["word"] = token
		out = append(out, w)
	}
	return append(out, words[len(words)-right:]...), nil
}
