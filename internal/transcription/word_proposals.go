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
	PrimaryFraction float64 `json:"primary_fraction,omitempty"`
	GapEvidence     bool    `json:"gap_evidence,omitempty"`
	ReviewChannel   string  `json:"review_channel,omitempty"`
	Index           int     `json:"index"`
	Start           float64 `json:"start"`
	End             float64 `json:"end"`
	Word            string  `json:"word"`
	Previous        string  `json:"previous"`
	Proposed        string  `json:"proposed"`
	Activity        float64 `json:"activity"`
	Margin          float64 `json:"margin"`
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
	known := map[string]bool{}
	if source.Diarization != nil {
		for _, turns := range [][]interfaces.DiarizationSegment{source.Diarization.Segments, source.Diarization.RegularSegments} {
			for _, turn := range turns {
				known[turn.Speaker] = true
			}
		}
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
		matches := word.Speaker != nil && *word.Speaker == p.Previous && strings.TrimSpace(p.Previous) != "" && !strings.EqualFold(strings.TrimSpace(p.Previous), "unknown") && !strings.EqualFold(strings.TrimSpace(p.Previous), "Speaker unclear")
		if p.GapEvidence {
			matches = word.Speaker == nil && !word.SpeakerReview && p.Previous == "" && p.ReviewChannel == ""
		}
		if !matches || word.Word != p.Word || word.Start != p.Start || word.End != p.End {
			return nil, fmt.Errorf("word proposal does not match source snapshot")
		}
		if p.ReviewChannel != "" {
			if p.Proposed != "" {
				return nil, fmt.Errorf("unmatched channel cannot assign an identity")
			}
		} else if !known[p.Proposed] || strings.TrimSpace(p.Proposed) == "" || strings.EqualFold(p.Proposed, "unknown") || strings.EqualFold(p.Proposed, "Speaker unclear") || p.Proposed == p.Previous {
			return nil, fmt.Errorf("invalid proposed identity")
		}
		if math.IsNaN(p.PrimaryFraction) || p.PrimaryFraction < 0 || p.PrimaryFraction > 1 {
			return nil, fmt.Errorf("invalid primary coverage")
		}
		minActivity := 0.9
		if p.GapEvidence && p.PrimaryFraction >= 0.8 && p.Margin >= 0.4 {
			minActivity = 0.65
		}
		if math.IsNaN(p.Activity) || math.IsInf(p.Activity, 0) || math.IsNaN(p.Margin) || math.IsInf(p.Margin, 0) || p.Activity < minActivity || p.Activity > 1 || p.Margin < 0.25 || p.Margin > 1 {
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
	unmatched, gaps := 0, 0
	for _, p := range proposals {
		speaker := p.Proposed
		if p.ReviewChannel != "" {
			result.WordSegments[p.Index].Speaker = nil
			result.WordSegments[p.Index].SpeakerReview = true
			unmatched++
		} else {
			result.WordSegments[p.Index].Speaker = &speaker
		}
		if p.GapEvidence {
			gaps++
		} else {
			first = math.Min(first, p.Start)
		}
	}
	result.Segments = machineSpeakerPassages(result.Segments, result.WordSegments, source.Diarization)
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
	// Recalculate coverage after reserving genuinely disputed turns for review.
	annotateOutcomes(&result, true, result.Diarization, nil)
	if unmatched > 0 {
		result.Metadata["word_refinement_unmatched_words"] = fmt.Sprint(unmatched)
	}
	result.Metadata["word_refinement_status"] = "applied_needs_review"
	result.Metadata["word_refinement_policy"] = wordRefinementPolicy
	result.Metadata["word_refinement_applied_words"] = fmt.Sprint(len(proposals) - unmatched)
	result.Metadata["word_refinement_confidence_kind"] = "uncalibrated frame activity; not person identity probability"
	if gaps > 0 {
		result.Metadata["word_refinement_gap_words"] = fmt.Sprint(gaps)
	}
	if gaps == len(proposals) {
		result.Metadata["word_refinement_status"] = "applied"
		return &result, nil
	}
	result.Metadata["diarization_status"] = "uncertain"
	result.Metadata["diarization_reason"] = "independent_model_disagreement"
	result.Metadata["speaker_review_reason"] = "refined_speaker_words"
	result.Metadata["speaker_review_start"] = fmt.Sprintf("%.6f", first)
	return &result, nil
}
