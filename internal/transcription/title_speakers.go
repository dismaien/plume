package transcription

import (
	"encoding/json"
	"regexp"
	"sort"
	"strings"

	"quill/internal/models"
)

var (
	titleBracketPattern      = regexp.MustCompile(`\s*\[[^\[\]\n]*\]`)
	titleDurationOnlyPattern = regexp.MustCompile(`^\{\d{1,2}:?\d{2}(?::\d{2})?\}$`)
	titleLocationPattern     = regexp.MustCompile(`\s+@`)
	titleDurationPattern     = regexp.MustCompile(`\s+\{\d{1,2}:?\d{2}(?::\d{2})?\}\s*$`)
)

type titleSpeakerSegment struct {
	Start       float64 `json:"start"`
	Speaker     *string `json:"speaker,omitempty"`
	SpeakerName *string `json:"speaker_name,omitempty"`
}

type titleSpeakerPayload struct {
	Segments []titleSpeakerSegment `json:"segments,omitempty"`
}

// SpeakerTitleNamesFromTranscript returns title-friendly speaker names in
// first-appearance order. Mappings win over stale speaker_name fields so a
// re-rename immediately updates derived titles.
func SpeakerTitleNamesFromTranscript(rawTranscript string, mappings []models.SpeakerMapping) []string {
	mappingBySpeaker := make(map[string]string, len(mappings))
	knownMappingSpeakers := make(map[string]struct{}, len(mappings))
	for _, mapping := range mappings {
		original := strings.TrimSpace(mapping.OriginalSpeaker)
		custom := strings.TrimSpace(mapping.CustomName)
		if original == "" {
			continue
		}
		knownMappingSpeakers[original] = struct{}{}
		if !mapping.IsConfirmedIdentity() || custom == "" || isGenericSpeakerLabel(custom) {
			continue
		}
		mappingBySpeaker[original] = custom
	}

	var names []string
	seen := map[string]bool{}
	add := func(name string) {
		short := SpeakerTitleName(name)
		if short == "" {
			return
		}
		key := strings.ToLower(short)
		if seen[key] {
			return
		}
		seen[key] = true
		names = append(names, short)
	}

	var payload titleSpeakerPayload
	if strings.TrimSpace(rawTranscript) != "" && json.Unmarshal([]byte(rawTranscript), &payload) == nil {
		for _, segment := range payload.Segments {
			if segment.Speaker != nil {
				speaker := strings.TrimSpace(*segment.Speaker)
				if mapped, ok := mappingBySpeaker[speaker]; ok {
					add(mapped)
					continue
				}
				if _, hasMapping := knownMappingSpeakers[speaker]; hasMapping {
					continue
				}
			}
			if segment.SpeakerName != nil {
				add(*segment.SpeakerName)
			}
		}
	}

	// Include mappings that were not represented in parsed segments, in stable
	// order, so a title still updates when transcript JSON is missing or partial.
	ordered := append([]models.SpeakerMapping(nil), mappings...)
	sort.SliceStable(ordered, func(i, j int) bool {
		return ordered[i].OriginalSpeaker < ordered[j].OriginalSpeaker
	})
	for _, mapping := range ordered {
		if mapping.IsConfirmedIdentity() {
			add(mapping.CustomName)
		}
	}

	return names
}

// SpeakerTitleName converts a display speaker name into the concise title form.
// It intentionally uses first names because archive titles are scan labels, not
// contact cards: "Ivan Vendrov" becomes "Ivan".
func SpeakerTitleName(displayName string) string {
	name := strings.TrimSpace(displayName)
	if name == "" || isGenericSpeakerLabel(name) {
		return ""
	}
	fields := strings.Fields(name)
	if len(fields) == 0 {
		return ""
	}
	short := strings.Trim(fields[0], " \t\r\n,;:")
	if short == "" || isGenericSpeakerLabel(short) {
		return ""
	}
	return short
}

// WithSpeakerTitleBlock replaces the derived speaker bracket in title, or
// inserts one before @location / {duration} when the title has no speaker block.
func WithSpeakerTitleBlock(title string, speakers []string) string {
	cleaned := normalizeSpeakerTitleNames(speakers)
	trimmed := strings.TrimSpace(title)
	if len(cleaned) == 0 {
		if loc := speakerBlockRange(trimmed); loc != nil {
			return strings.TrimSpace(trimmed[:loc[0]] + trimmed[loc[1]:])
		}
		return trimmed
	}

	block := " [" + strings.Join(cleaned, ", ") + "]"

	if loc := speakerBlockRange(trimmed); loc != nil {
		return strings.TrimSpace(trimmed[:loc[0]]) + block + trimmed[loc[1]:]
	}

	insertAt := len(trimmed)
	if loc := titleLocationPattern.FindStringIndex(trimmed); loc != nil {
		insertAt = loc[0]
	}
	if loc := titleDurationPattern.FindStringIndex(trimmed); loc != nil && loc[0] < insertAt {
		insertAt = loc[0]
	}

	return strings.TrimSpace(strings.TrimSpace(trimmed[:insertAt]) + block + trimmed[insertAt:])
}

func speakerBlockRange(title string) []int {
	for _, loc := range titleBracketPattern.FindAllStringIndex(title, -1) {
		suffix := strings.TrimSpace(title[loc[1]:])
		if suffix == "" || strings.HasPrefix(suffix, "@") || titleDurationOnlyPattern.MatchString(suffix) {
			return loc
		}
	}
	return nil
}

func normalizeSpeakerTitleNames(speakers []string) []string {
	var cleaned []string
	seen := map[string]bool{}
	for _, speaker := range speakers {
		name := SpeakerTitleName(speaker)
		if name == "" {
			continue
		}
		key := strings.ToLower(name)
		if seen[key] {
			continue
		}
		seen[key] = true
		cleaned = append(cleaned, name)
	}
	return cleaned
}

func isGenericSpeakerLabel(name string) bool {
	normalized := strings.ToLower(strings.TrimSpace(name))
	if normalized == "" || normalized == "unknown" {
		return true
	}
	normalized = strings.ReplaceAll(normalized, "_", " ")
	normalized = strings.ReplaceAll(normalized, "-", " ")
	normalized = strings.Join(strings.Fields(normalized), " ")
	if normalized == "speaker" {
		return true
	}
	return strings.HasPrefix(normalized, "speaker ")
}
