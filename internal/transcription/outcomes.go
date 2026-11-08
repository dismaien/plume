package transcription

import (
	"fmt"
	"quill/internal/transcription/interfaces"
	"strings"
)

// Outcomes describe evidence, not accuracy. Coverage is never a quality score.
func annotateOutcomes(result *interfaces.TranscriptResult, requested bool, diar *interfaces.DiarizationResult, diarErr error) {
	if result.Metadata == nil {
		result.Metadata = map[string]string{}
	}
	m := result.Metadata
	m["speaker_assignment_policy"] = "overlap_union_majority_v1"
	if m["word_assignment_timeline"] == "regular_overlap_aware" {
		m["speaker_assignment_policy"] = "overlap_union_majority_v2_regular_tracks_experimental"
	}
	m["text_status"] = "ready"
	if strings.TrimSpace(result.Text) == "" {
		m["text_status"] = "needs_review"
		m["text_reason"] = "empty_output_not_verified_silence"
	}
	if m["alignment_status"] == "" {
		m["alignment_status"] = "not_available"
	}
	if len(result.WordSegments) > 0 && m["alignment_status"] != "failed" {
		m["alignment_status"] = "ready"
	}
	m["diarization_status"] = "not_requested"
	if requested {
		m["diarization_status"] = "uncertain"
		m["diarization_reason"] = "no_usable_speaker_result"
		if diarErr != nil {
			m["diarization_status"] = "failed"
			m["diarization_reason"] = diarErr.Error()
		}
		if diar != nil {
			result.Diarization = diar
			m["diarization_engine"] = diar.ModelUsed
			for k, v := range diar.Metadata {
				m[diarizationMetadataKey(k)] = v
			}
			if status := diar.Metadata["speaker_identity_status"]; status != "" {
				m["diarization_status"] = status
			}
			if reason := diar.Metadata["speaker_identity_reason"]; reason != "" {
				m["diarization_reason"] = reason
			}
			if diar.SpeakerCount > 0 && m["diarization_status"] != "uncertain" {
				delete(m, "diarization_reason")
			}
			if diar.SpeakerCount > 0 && diar.Metadata["speaker_identity_status"] == "" {
				m["diarization_status"] = "accepted"
				delete(m, "diarization_reason")
			}
		}
	}
	total, labelled := len(result.WordSegments), 0
	attributedSpeakers := map[string]bool{}
	for _, w := range result.WordSegments {
		if w.Speaker != nil && *w.Speaker != "" {
			labelled++
			attributedSpeakers[*w.Speaker] = true
		}
	}
	if total == 0 {
		total = len(result.Segments)
		for _, s := range result.Segments {
			if s.Speaker != nil && *s.Speaker != "" {
				labelled++
				attributedSpeakers[*s.Speaker] = true
			}
		}
	}
	m["speaker_labelled_units"] = fmt.Sprint(labelled)
	m["speaker_total_units"] = fmt.Sprint(total)
	m["speaker_unattributed_units"] = fmt.Sprint(total - labelled)
	m["speaker_unit_kind"] = "segments"
	if len(result.WordSegments) > 0 {
		m["speaker_unit_kind"] = "words"
	}
	if total > 0 {
		m["speaker_labelled_coverage"] = fmt.Sprintf("%.6f", float64(labelled)/float64(total))
	}
	if requested && (total == 0 || labelled < total) && m["diarization_status"] != "failed" {
		m["diarization_status"] = "uncertain"
		if total > 0 && labelled < total && m["diarization_reason"] == "" {
			m["diarization_reason"] = "partial_speaker_attribution"
		}
	}
	if requested && diar != nil {
		m["speaker_detected_count"] = fmt.Sprint(diar.SpeakerCount)
		m["speaker_attributed_count"] = fmt.Sprint(len(attributedSpeakers))
		if total > 0 && diar.SpeakerCount > len(attributedSpeakers) && m["diarization_status"] != "failed" {
			m["diarization_status"] = "uncertain"
			m["speaker_review_reason"] = "detected_voice_has_no_attributed_words"
			if m["diarization_reason"] == "" {
				m["diarization_reason"] = "detected_speaker_missing_from_word_attribution"
			}
			found, start := false, 0.0
			for _, timeline := range [][]interfaces.DiarizationSegment{diar.Segments, diar.RegularSegments} {
				for _, turn := range timeline {
					if turn.Speaker != "" && !attributedSpeakers[turn.Speaker] && (!found || turn.Start < start) {
						found, start = true, turn.Start
					}
				}
			}
			if found {
				m["speaker_review_start"] = fmt.Sprintf("%.6f", start)
			}
		}
	}
	if diarErr != nil {
		m["diarization_status"] = "failed"
		m["diarization_reason"] = diarErr.Error()
	}

}

func diarizationMetadataKey(key string) string {
	switch key {
	case "model_id", "model_family", "model_version", "adapter_type", "device", "batch_size":
		return "diarization_" + key
	}
	return key
}

// AssembleDiarizedTranscript exposes the production merge for reproducible local
// evaluation with frozen ASR words. It performs no database or filesystem work.
func AssembleDiarizedTranscript(transcript *interfaces.TranscriptResult, diarization *interfaces.DiarizationResult) *interfaces.TranscriptResult {
	service := &UnifiedTranscriptionService{}
	result := service.mergeDiarizationWithTranscription(transcript, diarization)
	annotateOutcomes(result, true, diarization, nil)
	return result
}

// AssembleWithOverlapReview is an experimental, conservative evaluation policy.
// The public benchmark found a precision/coverage tradeoff, so it is not the
// production default and cannot silently replace reviewed transcript labels.
func AssembleWithOverlapReview(transcript *interfaces.TranscriptResult, diarization *interfaces.DiarizationResult) *interfaces.TranscriptResult {
	service := &UnifiedTranscriptionService{}
	result := service.mergeDiarizationUsingPolicy(transcript, diarization, true)
	annotateOutcomes(result, true, diarization, nil)
	return result
}
