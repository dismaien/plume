package transcriptedit

import (
	"encoding/json"
	"math"
	"regexp"
	"slices"
	"strings"
	"unicode"
)

type PassageReview struct {
	Segment   int    `json:"segment"`
	FirstWord *int   `json:"first_word"`
	LastWord  *int   `json:"last_word"`
	Original  string `json:"original"`
	Text      string `json:"text"`
	Speaker   string `json:"speaker"`
	Cue       bool   `json:"cue"`
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

// Find the exact text span, disambiguating repeated phrases by their position among
// the source words. Reject an ambiguous legacy alignment instead of editing a neighbor.
func reviewSpan(section string, words []reviewItem, first, last int, sectionStart float64) (int, int, bool) {
	spans := reviewTokens.FindAllStringIndex(section, -1)
	wanted := []string{}
	for _, w := range words[first : last+1] {
		wanted = append(wanted, strings.Fields(valueText(w, "word"))...)
	}
	if len(wanted) == 0 {
		return 0, 0, false
	}
	matches := []int{}
	for i := 0; i+len(wanted) <= len(spans); i++ {
		match := true
		for j, s := range wanted {
			r := spans[i+j]
			if section[r[0]:r[1]] != s {
				match = false
				break
			}
		}
		if match {
			matches = append(matches, i)
		}
	}
	if len(matches) == 0 {
		return 0, 0, false
	}
	pos := matches[0]
	if len(matches) > 1 {
		index := 0
		for i := first - 1; i >= 0; i-- {
			start, ok := words[i]["start"].(float64)
			if !ok || start < sectionStart {
				break
			}
			index += len(strings.Fields(valueText(words[i], "word")))
		}
		if !slices.Contains(matches, index) {
			return 0, 0, false
		}
		pos = index
	}
	return spans[pos][0], spans[pos+len(wanted)-1][1], true
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
	var lo, hi int
	var start, end float64
	if r.FirstWord == nil || r.LastWord == nil {
		if len(words) > 0 || seg == nil || r.FirstWord != nil || r.LastWord != nil || strings.TrimSpace(valueText(seg, "text")) != original {
			return "", ErrInvalid
		}
		reviewed(seg, speaker)
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
			var ok bool
			lo, hi, ok = reviewSpan(valueText(seg, "text"), words, first, last, ss)
			if !ok {
				return "", ErrInvalid
			}
			section := valueText(seg, "text")
			before, after := section[:lo], section[hi:]
			fullOffset += len(strings.Fields(before))
			sectionEnd, _ := seg["end"].(float64)
			if sectionEnd < ss || end < ss || start > sectionEnd {
				return "", ErrInvalid
			}
			splitStart, splitEnd := math.Max(start, ss), math.Min(end, sectionEnd)
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
		if original == text {
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
			spans := reviewTokens.FindAllStringIndex(full, -1)
			old := strings.Fields(original)
			matches := func(pos int) bool {
				if pos < 0 || pos+len(old) > len(spans) || len(old) == 0 {
					return false
				}
				for j, token := range old {
					span := spans[pos+j]
					if full[span[0]:span[1]] != token {
						return false
					}
				}
				return true
			}
			if !matches(fullOffset) {
				fullOffset = -1
				for i := range spans {
					if matches(i) {
						if fullOffset != -1 {
							return "", ErrConflict
						}
						fullOffset = i
					}
				}
				if fullOffset < 0 {
					return "", ErrConflict
				}
			}
			full = full[:spans[fullOffset][0]] + text + full[spans[fullOffset+len(old)-1][1]:]
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
	if r.Cue {
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
