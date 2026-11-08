package transcription

import (
	"encoding/json"
	"math"
	"quill/internal/transcription/interfaces"
	"quill/internal/transcription/pipeline"
)

func retainedSpeechRegions(value string) []pipeline.SpeechRegion {
	var regions []pipeline.SpeechRegion
	if value == "" || json.Unmarshal([]byte(value), &regions) != nil {
		return nil
	}
	last := 0.0
	for _, r := range regions {
		if math.IsNaN(r.Start) || math.IsNaN(r.End) || math.IsInf(r.Start, 0) || math.IsInf(r.End, 0) || r.Start < last || r.End <= r.Start {
			return nil
		}
		last = r.End
	}
	return regions
}

// Only VAD splice-crossing words need a different clock. Reinserted silence
// must not dilute acoustic support. Preserve all text/timestamps and require
// agreement across independently retained pieces, including regular overlap
// tracks. This is acoustic evidence, never a carry-forward text heuristic.
func (u *UnifiedTranscriptionService) findWordSpeakerWithSpeechClock(word interfaces.TranscriptWord, turns, regular []interfaces.DiarizationSegment, regions, retained []pipeline.SpeechRegion) string {
	var pieces []pipeline.SpeechRegion
	for _, r := range regions {
		a, b := max(word.Start, r.Start), min(word.End, r.End)
		if b > a {
			pieces = append(pieces, pipeline.SpeechRegion{Start: a, End: b})
		}
	}
	cutPieces := 0
	for _, r := range retained {
		if min(word.End, r.End) > max(word.Start, r.Start) {
			cutPieces++
		}
	}
	if cutPieces < 2 {
		return u.findBestSpeakerForSegment(word.Start, word.End, turns)
	}
	if len(pieces) == 0 {
		return ""
	}
	compress := func(input []interfaces.DiarizationSegment) ([]interfaces.DiarizationSegment, float64) {
		var out []interfaces.DiarizationSegment
		offset := 0.0
		for _, p := range pieces {
			for _, t := range input {
				a, b := max(p.Start, t.Start), min(p.End, t.End)
				if b > a {
					copyTurn := t
					copyTurn.Start = offset + a - p.Start
					copyTurn.End = offset + b - p.Start
					out = append(out, copyTurn)
				}
			}
			offset += p.End - p.Start
		}
		return out, offset
	}
	compressed, duration := compress(turns)
	speaker := u.findBestSpeakerForSegment(0, duration, compressed)
	if speaker == "" {
		return ""
	}
	for _, p := range pieces {
		if other := u.findBestSpeakerForSegment(p.Start, p.End, turns); other != "" && other != speaker {
			return ""
		}
	}
	if len(regular) > 0 {
		all, _ := compress(regular)
		if u.findBestSpeakerForSegment(0, duration, all) != speaker {
			return ""
		}
	}
	return speaker
}
