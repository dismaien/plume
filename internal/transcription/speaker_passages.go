package transcription

import (
	"slices"
	"strings"
	"unicode"
	"unicode/utf8"

	"quill/internal/transcription/interfaces"
)

// Only assemble machine output. Word-level speaker evidence remains available
// for review even when a short gap stays inside a spoken sentence.
func machineSpeakerPassages(segments []interfaces.TranscriptSegment, words []interfaces.TranscriptWord, diar *interfaces.DiarizationResult) []interfaces.TranscriptSegment {
	displayWords := slices.Clone(words)
	for i, w := range words {
		if w.Speaker == nil && !w.SpeakerReview {
			displayWords[i].Speaker = passageSpeaker(words, i, diar)
		}
	}
	parts := splitSegmentsAtWordSpeakerChanges(segments, displayWords)
	out := make([]interfaces.TranscriptSegment, 0, len(parts))
	for _, part := range parts {
		person := transcriptSegmentSpeaker(part)
		if len(out) > 0 && person != "" {
			prev := &out[len(out)-1]
			if transcriptSegmentSpeaker(*prev) == person && continuesSentence(*prev, part) {
				prev.Text += part.Text
				prev.End = max(prev.End, part.End)
				continue
			}
		}
		out = append(out, part)
	}
	return out
}

// A missing word label may remain in a readable turn when both neighbours
// agree, or when a nearby neighbour agrees with unopposed acoustic support.
// Always consult the original words, so a guess cannot propagate along a gap.
func passageSpeaker(words []interfaces.TranscriptWord, i int, diar *interfaces.DiarizationResult) *string {
	w := words[i]
	duration := w.End - w.Start
	if diar == nil || len(diar.Segments) == 0 || duration <= 0 || duration > 5 || len(strings.Fields(w.Word)) != 1 || isLikelyBackchannel(w.Word) {
		return nil
	}
	// Overlapping ASR words do not establish a linear turn boundary.
	if (i > 0 && words[i-1].End > w.Start) || (i+1 < len(words) && words[i+1].Start < w.End) {
		return nil
	}
	var left, right *string
	if i > 0 {
		prev := words[i-1]
		if prev.End <= w.End && prev.Start <= w.Start && w.Start-prev.End <= .35 && !endsSentence(prev.Word) {
			left = prev.Speaker
		}
	}
	if i+1 < len(words) {
		next := words[i+1]
		if next.Start >= w.Start && next.End >= w.End && next.Start-w.End <= .35 && !endsSentence(w.Word) {
			right = next.Speaker
		}
	}
	// A pause before an onset can mark a handover. The following voice
	// alone is insufficient to attach a word across that break.
	if left == nil && (right == nil || i == 0 || w.Start-words[i-1].End > .65) {
		return nil
	}
	same := left != nil && right != nil && *left == *right
	start, end := w.Start, w.End
	if same {
		start, end = min(start, words[i-1].End), max(end, words[i+1].Start)
	}
	// Short ambiguous interjections can be present only in the regular
	// (overlap-preserving) timeline. An opposing turn vetoes the projection.
	// A timestamp stretched across a pause can still have voice activity at
	// both ends. Require support on both sides for such long words.
	var voice string
	var support float64
	startHalf, endHalf := false, false
	middle := wordMidpoint(w)
	for _, turns := range [][]interfaces.DiarizationSegment{diar.Segments, diar.RegularSegments} {
		for _, turn := range turns {
			if turn.End <= start || turn.Start >= end {
				continue
			}
			if turn.Speaker == "" || (voice != "" && turn.Speaker != voice) || (same && turn.Speaker != *left) {
				return nil
			}
			voice = turn.Speaker
			a, b := max(w.Start, turn.Start), min(w.End, turn.End)
			support = max(support, b-a)
			if b > a {
				startHalf = startHalf || a < middle
				endHalf = endHalf || b > middle
			}
		}
	}
	if same && end-start <= 1 {
		return left
	}
	if support < duration*.25 || duration > 1 && !(startHalf && endHalf) {
		return nil
	}
	if left != nil && voice == *left {
		return left
	}
	if right != nil && voice == *right {
		return right
	}
	return nil
}

func continuesSentence(left, right interfaces.TranscriptSegment) bool {
	// Retain paragraph, language and pause boundaries. Refuse to glue words
	// together when an old payload relies on its row separator for whitespace.
	if left.Text == "" || right.Text == "" || endsSentence(left.Text) || right.Start < left.Start || right.Start < left.End-.10 ||
		right.Start-left.End > 0.65 {
		return false
	}
	if (left.Language == nil) != (right.Language == nil) || left.Language != nil && *left.Language != *right.Language {
		return false
	}
	last, _ := utf8.DecodeLastRuneInString(left.Text)
	first, _ := utf8.DecodeRuneInString(right.Text)
	leftSpace := left.Text[len(strings.TrimRightFunc(left.Text, unicode.IsSpace)):]
	rightSpace := right.Text[:len(right.Text)-len(strings.TrimLeftFunc(right.Text, unicode.IsSpace))]
	return (unicode.IsSpace(last) || unicode.IsSpace(first)) &&
		!strings.ContainsAny(leftSpace, "\r\n") && !strings.ContainsAny(rightSpace, "\r\n")
}
