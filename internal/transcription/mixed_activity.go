package transcription

import (
	"encoding/json"
	"fmt"
	"math"
	"slices"
	"sort"

	"quill/internal/transcription/interfaces"
)

// Each recovered span is tied to one retained mixed turn in the primary output.
type MixedActivitySpan struct {
	Index int `json:"index"`
	interfaces.DiarizationSegment
}

type AcousticWordRefinement struct {
	Proposals     []SpeakerWordProposal `json:"proposals"`
	MixedActivity struct {
		Exclusive []MixedActivitySpan `json:"mixed_segments"`
		Regular   []MixedActivitySpan `json:"mixed_regular_segments"`
	} `json:"mixed_activity"`
}

func ApplyAcousticWordRefinement(source *interfaces.TranscriptResult, batch AcousticWordRefinement) (*interfaces.TranscriptResult, error) {
	// Validate proposals against the unchanged snapshot that the native model read.
	primary, err := ApplySpeakerWordProposals(source, batch.Proposals)
	if err != nil || len(batch.MixedActivity.Exclusive)+len(batch.MixedActivity.Regular) == 0 {
		return primary, err
	}
	if source.Diarization == nil {
		return nil, fmt.Errorf("missing mixed-track source")
	}
	diar := *source.Diarization
	var targets map[string][]string
	if err := json.Unmarshal([]byte(diar.Metadata["speaker_mixed_tracks"]), &targets); err != nil {
		return nil, fmt.Errorf("invalid mixed-track identities: %w", err)
	}
	restore := func(known, mixed []interfaces.DiarizationSegment, spans []MixedActivitySpan) ([]interfaces.DiarizationSegment, error) {
		restored := slices.Clone(known)
		seen := make(map[interfaces.DiarizationSegment]bool, len(known))
		for _, turn := range known {
			seen[turn] = true
		}
		for _, span := range spans {
			if span.Index < 0 || span.Index >= len(mixed) {
				return nil, fmt.Errorf("unknown mixed turn")
			}
			r := mixed[span.Index]
			if !slices.Contains(targets[r.Speaker], span.Speaker) || !slices.Contains(diar.Speakers, span.Speaker) ||
				math.IsNaN(span.Start+span.End) || math.IsInf(span.Start+span.End, 0) ||
				span.Start < r.Start || span.End > r.End || span.Start >= span.End {
				return nil, fmt.Errorf("recovered activity outside source turn or speaker set")
			}
			if !seen[span.DiarizationSegment] {
				restored = append(restored, span.DiarizationSegment)
				seen[span.DiarizationSegment] = true
			}
		}
		sort.SliceStable(restored, func(i, j int) bool { return restored[i].Start < restored[j].Start })
		return restored, nil
	}
	diar.Segments, err = restore(diar.Segments, diar.MixedSegments, batch.MixedActivity.Exclusive)
	if err != nil {
		return nil, err
	}
	diar.RegularSegments, err = restore(diar.RegularSegments, diar.MixedRegularSegments, batch.MixedActivity.Regular)
	if err != nil {
		return nil, err
	}
	result := AssembleDiarizedTranscript(source, &diar)
	// A newly supported primary turn can change the premise of an earlier
	// proposal. Reconcile disagreement explicitly instead of moving it to a
	// different word or overwriting the new boundary.
	var remaining []SpeakerWordProposal
	for _, p := range batch.Proposals {
		current := result.WordSegments[p.Index].Speaker
		if p.GapEvidence {
			if current == nil && !result.WordSegments[p.Index].SpeakerReview {
				remaining = append(remaining, p)
			}
			continue
		}
		if current == nil || (*current == p.Proposed && p.ReviewChannel == "") {
			continue
		}
		if *current != p.Previous {
			p.Previous, p.Proposed, p.ReviewChannel = *current, "", "mixed_activity_disagreement"
		}
		remaining = append(remaining, p)
	}
	result.Metadata["word_refinement_status"] = "applied_needs_review"
	result.Metadata["word_refinement_policy"] = wordRefinementPolicy
	result.Metadata["mixed_activity_recovered_intervals"] = fmt.Sprint(len(batch.MixedActivity.Exclusive))
	return ApplySpeakerWordProposals(result, remaining)
}
