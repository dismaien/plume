package transcription

import (
	"fmt"
	"math"
	"quill/internal/transcription/interfaces"
	"strings"
)

// SpeakerWordProposal is a bounded correction to a fresh machine transcript.
// Activity and Margin are model activity evidence, not identity probabilities.
type SpeakerWordProposal struct {
	Index    int     `json:"index"`
	Start    float64 `json:"start"`
	End      float64 `json:"end"`
	Word     string  `json:"word"`
	Previous string  `json:"previous"`
	Proposed string  `json:"proposed"`
	Activity float64 `json:"activity"`
	Margin   float64 `json:"margin"`
}

// ApplySpeakerWordProposals validates the whole batch before changing a copy.
// Explicit unknowns, text, timing and input objects remain unchanged.
func ApplySpeakerWordProposals(source *interfaces.TranscriptResult, proposals []SpeakerWordProposal) (*interfaces.TranscriptResult, error) {
	if source == nil {
		return nil, fmt.Errorf("missing source transcript")
	}
	if len(proposals) == 0 {
		return source, nil
	}
	seen := map[int]bool{}
	for _, p := range proposals {
		if p.Index < 0 || p.Index >= len(source.WordSegments) || seen[p.Index] {
			return nil, fmt.Errorf("invalid or duplicate word proposal index")
		}
		seen[p.Index] = true
		word := source.WordSegments[p.Index]
		if math.IsNaN(p.Start) || math.IsInf(p.Start, 0) || math.IsNaN(p.End) || math.IsInf(p.End, 0) || p.Start < 0 || p.End < p.Start {
			return nil, fmt.Errorf("invalid proposal timeline")
		}
		if word.Speaker == nil || strings.TrimSpace(*word.Speaker) == "" || strings.EqualFold(strings.TrimSpace(*word.Speaker), "unknown") || strings.EqualFold(strings.TrimSpace(*word.Speaker), "Speaker unclear") || *word.Speaker != p.Previous || word.Word != p.Word || word.Start != p.Start || word.End != p.End {
			return nil, fmt.Errorf("word proposal does not match source snapshot")
		}
		if strings.TrimSpace(p.Proposed) == "" || strings.EqualFold(p.Proposed, "unknown") || strings.EqualFold(p.Proposed, "Speaker unclear") || p.Proposed == p.Previous {
			return nil, fmt.Errorf("invalid proposed identity")
		}
		if math.IsNaN(p.Activity) || math.IsInf(p.Activity, 0) || math.IsNaN(p.Margin) || math.IsInf(p.Margin, 0) || p.Activity < 0.9 || p.Activity > 1 || p.Margin < 0.25 || p.Margin > 1 {
			return nil, fmt.Errorf("insufficient or invalid activity evidence")
		}
	}
	result := *source
	result.WordSegments = append([]interfaces.TranscriptWord(nil), source.WordSegments...)
	result.Segments = append([]interfaces.TranscriptSegment(nil), source.Segments...)
	result.Metadata = map[string]string{}
	for key, value := range source.Metadata {
		result.Metadata[key] = value
	}
	first := math.Inf(1)
	for _, p := range proposals {
		speaker := p.Proposed
		result.WordSegments[p.Index].Speaker = &speaker
		first = math.Min(first, p.Start)
	}
	result.Segments = splitSegmentsAtWordSpeakerChanges(result.Segments, result.WordSegments)
	var before, after strings.Builder
	for _, segment := range source.Segments {
		before.WriteString(segment.Text)
	}
	for _, segment := range result.Segments {
		after.WriteString(segment.Text)
	}
	if before.String() != after.String() {
		return nil, fmt.Errorf("word refinement changed source segment text")
	}
	result.Metadata["word_refinement_status"] = "applied_needs_review"
	result.Metadata["word_refinement_policy"] = "known_machine_labels_v1"
	result.Metadata["word_refinement_applied_words"] = fmt.Sprint(len(proposals))
	result.Metadata["word_refinement_confidence_kind"] = "uncalibrated frame activity; not person identity probability"
	result.Metadata["diarization_status"] = "uncertain"
	result.Metadata["diarization_reason"] = "independent_model_disagreement"
	result.Metadata["speaker_review_reason"] = "refined_speaker_words"
	result.Metadata["speaker_review_start"] = fmt.Sprintf("%.6f", first)
	people := map[string]bool{}
	for _, word := range result.WordSegments {
		if word.Speaker != nil && *word.Speaker != "" {
			people[*word.Speaker] = true
		}
	}
	result.Metadata["speaker_attributed_count"] = fmt.Sprint(len(people))
	return &result, nil
}
