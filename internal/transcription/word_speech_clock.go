package transcription

import (
	"encoding/json"
	"math"
	"sort"
	"strings"

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

// VAD splice-crossing words need a different clock. Reinserted silence
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
		speaker := u.findBestSpeakerForSegment(word.Start, word.End, turns)
		if speaker != "" || len(regular) == 0 {
			return speaker
		}
		return unopposedWordEdge(word, turns, regular)
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

// An aligned word can extend past the detected start or end of a voice.
// Require one continuous piece of that voice in both primary timelines and
// veto any competing activity anywhere inside the word, including overlap.
func unopposedWordEdge(word interfaces.TranscriptWord, exclusive, regular []interfaces.DiarizationSegment) string {
	duration := word.End - word.Start
	if duration <= 0 || duration > 2 || len(regular) == 0 || len(strings.Fields(word.Word)) != 1 {
		return ""
	}
	required := max(.25*duration, min(.08, .4*duration))
	person := ""
	for _, timeline := range [][]interfaces.DiarizationSegment{exclusive, regular} {
		var pieces []pipeline.SpeechRegion
		for _, turn := range timeline {
			overlap := min(word.End, turn.End) - max(word.Start, turn.Start)
			if overlap <= 0 {
				continue
			}
			if turn.Speaker == "" || (person != "" && person != turn.Speaker) {
				return ""
			}
			person = turn.Speaker
			pieces = append(pieces, pipeline.SpeechRegion{Start: max(word.Start, turn.Start), End: min(word.End, turn.End)})
		}
		sort.Slice(pieces, func(i, j int) bool { return pieces[i].Start < pieces[j].Start })
		longest, a, b := 0.0, 0.0, -1.0
		for _, p := range pieces {
			if p.Start > b {
				a, b = p.Start, p.End
			} else {
				b = max(b, p.End)
			}
			longest = max(longest, b-a)
		}
		// The independent activity clock advances in 10 ms frames. A half
		// frame at an endpoint cannot reliably distinguish two support ratios.
		if longest <= 0 || longest+.005 < required {
			return ""
		}
	}
	return person
}

// Match an otherwise unresolved backchannel to a complete, separate voice
// turn. The reply must be the only word that can claim that acoustic turn.
func restoreIsolatedReplies(words []interfaces.TranscriptWord, exclusive, regular []interfaces.DiarizationSegment) {
	for i := 1; i+1 < len(words); i++ {
		prev, w, next := words[i-1], words[i], words[i+1]
		if w.Speaker != nil || !isLikelyBackchannel(w.Word) || w.End <= w.Start || w.End-w.Start > 1.5 ||
			prev.Speaker == nil || next.Speaker == nil || *prev.Speaker != *next.Speaker ||
			prev.End > w.Start || next.Start < w.End || w.Start-prev.End > 1 || next.Start-w.End > 1 {
			continue
		}
		var candidate *interfaces.DiarizationSegment
		for j := range exclusive {
			t := &exclusive[j]
			if t.Start >= w.End || t.End <= w.Start || t.Speaker == *prev.Speaker {
				continue
			}
			overlap := min(t.End, w.End) - max(t.Start, w.Start)
			if candidate != nil || t.Speaker == "" || t.Start < prev.End || t.End > next.Start ||
				t.End-t.Start > 1 || overlap < .12 || overlap < .5*(t.End-t.Start) {
				candidate = nil
				break
			}
			candidate = t
		}
		if candidate == nil {
			continue
		}
		// A competing regular track during the isolated turn is a real
		// alternative, even when exclusive diarization picked a winner.
		reply := interfaces.TranscriptWord{Word: w.Word, Start: candidate.Start, End: candidate.End}
		if person := unopposedWordEdge(reply, []interfaces.DiarizationSegment{*candidate}, regular); person == candidate.Speaker {
			words[i].Speaker = &person
		}
	}
}
