package transcription

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"time"
	"unicode"

	"quill/internal/transcription/interfaces"
)

// WordTimeObservation locates a source token in an independent excerpt decode.
// Onset and Offset locate speech for attribution only. They are never stored
// in place of the user's original alignment or used to rewrite source text.
type WordTimeObservation struct {
	Index  int     `json:"index"`
	Word   string  `json:"word"`
	Start  float64 `json:"start"`
	End    float64 `json:"end"`
	Onset  float64 `json:"onset"`
	Offset float64 `json:"offset"`
}

type WordTimeObservations struct {
	InputSHA256 string                `json:"input_sha256"`
	Observed    []WordTimeObservation `json:"observed"`
	Excerpts    int                   `json:"excerpts"`
}

func soleObservedSpeaker(start, end float64, turns []interfaces.DiarizationSegment, minCoverage float64) string {
	var covered []interfaces.DiarizationSegment
	person := ""
	for _, t := range turns {
		a, b := max(start, t.Start), min(end, t.End)
		if b <= a {
			continue
		}
		id := strings.TrimSpace(t.Speaker)
		if id == "" || strings.EqualFold(id, "unknown") || strings.EqualFold(id, "Speaker unclear") || person != "" && person != t.Speaker {
			return ""
		}
		person = t.Speaker
		if minCoverage > 0 {
			covered = append(covered, interfaces.DiarizationSegment{Start: a, End: b})
		}
	}
	if minCoverage == 0 {
		return person
	}
	sort.Slice(covered, func(i, j int) bool { return covered[i].Start < covered[j].Start })
	last, seconds := start, 0.
	for _, t := range covered {
		seconds += max(0., t.End-max(last, t.Start))
		last = max(last, t.End)
	}
	if seconds < minCoverage*(end-start) {
		return ""
	}
	return person
}

func overlapsRepeatedWord(words []interfaces.TranscriptWord, o WordTimeObservation) bool {
	normalize := func(v string) string {
		return strings.ToLower(strings.TrimFunc(v, func(r rune) bool { return unicode.IsSpace(r) || unicode.IsPunct(r) }))
	}
	for _, j := range []int{o.Index - 1, o.Index + 1} {
		if j < 0 || j >= len(words) {
			continue
		}
		other := words[j]
		if normalize(other.Word) == normalize(o.Word) && min(other.End, o.Offset)-max(other.Start, o.Onset) > .25*(o.Offset-o.Onset) {
			return true
		}
	}
	return false
}

// ApplyObservedWordTimes only fills previously unresolved machine words. Both
// primary timelines must identify one voice at the new word location.
func ApplyObservedWordTimes(source *interfaces.TranscriptResult, batch WordTimeObservations) (*interfaces.TranscriptResult, error) {
	if source == nil || source.Diarization == nil {
		return source, fmt.Errorf("missing primary timeline")
	}
	result := *source
	result.WordSegments = slices.Clone(source.WordSegments)
	applied := 0
	last := -1
	for _, o := range batch.Observed {
		if o.Index <= last || o.Index < 0 || o.Index >= len(result.WordSegments) {
			return source, fmt.Errorf("invalid observed word index")
		}
		last = o.Index
		w := &result.WordSegments[o.Index]
		if w.Word != o.Word || w.Start != o.Start || w.End != o.End || w.Speaker != nil || w.SpeakerReview {
			return source, fmt.Errorf("observation does not match fresh unassigned word")
		}
		if math.IsNaN(o.Onset+o.Offset) || math.IsInf(o.Onset+o.Offset, 0) || o.Onset < 0 || o.Offset <= o.Onset || o.Offset-o.Onset > 1.5 || o.Offset < w.Start-.2 || o.Onset > w.End+.2 || math.Min(math.Abs(o.Onset-w.Start), math.Abs(o.Offset-w.End)) > .8 {
			return source, fmt.Errorf("invalid observed token time")
		}
		// A duplicated ASR token must not borrow the same audible reply.
		if overlapsRepeatedWord(source.WordSegments, o) {
			continue
		}
		person := soleObservedSpeaker(o.Onset, o.Offset, source.Diarization.RegularSegments, .65)
		if person != "" && soleObservedSpeaker(o.Onset, o.Offset, source.Diarization.Segments, .65) == person && soleObservedSpeaker(w.Start, w.End, source.Diarization.RegularSegments, 0) == person {
			w.Speaker = &person
			applied++
		}
	}
	result.Metadata = make(map[string]string, len(source.Metadata)+2)
	for key, v := range source.Metadata {
		result.Metadata[key] = v
	}
	if applied > 0 {
		result.Segments = machineSpeakerPassages(source.Segments, result.WordSegments, source.Diarization)
		var before, after strings.Builder
		for _, s := range source.Segments {
			before.WriteString(s.Text)
		}
		for _, s := range result.Segments {
			after.WriteString(s.Text)
		}
		if before.String() != after.String() {
			return source, fmt.Errorf("observation changed transcript text")
		}
		annotateOutcomes(&result, true, source.Diarization, nil)
		if source.Metadata["diarization_reason"] == "independent_model_disagreement" {
			result.Metadata["diarization_reason"] = "independent_model_disagreement"
			result.Metadata["diarization_status"] = "uncertain"
		}
	}
	result.Metadata["short_word_timing_applied_words"] = fmt.Sprint(applied)
	result.Metadata["short_word_timing_excerpts"] = fmt.Sprint(batch.Excerpts)
	return &result, nil
}

func (u *UnifiedTranscriptionService) checkShortWordTimes(ctx context.Context, result *interfaces.TranscriptResult, audio, directory, scripts string) (*interfaces.TranscriptResult, error) {
	if result.Language != "en" || !slices.ContainsFunc(result.WordSegments, func(w interfaces.TranscriptWord) bool { return w.Speaker == nil && !w.SpeakerReview }) {
		return result, nil
	}
	python := filepath.Join(u.envPath, "mlx-whisper", "mlx-whisper", ".venv", "bin", "python")
	if _, err := os.Stat(python); os.IsNotExist(err) {
		return result, nil
	} else if err != nil {
		return result, err
	}
	snapshot, err := json.Marshal(result)
	if err != nil {
		return result, err
	}
	input, output := filepath.Join(directory, "word-time-input.json"), filepath.Join(directory, "word-times.json")
	if err = os.WriteFile(input, snapshot, 0600); err != nil {
		return result, err
	}
	run, cancel := context.WithTimeout(ctx, 15*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(run, python, filepath.Join(scripts, "short_word_times.py"), "--audio", audio, "--input", input, "--out", output)
	cmd.Env = nativeWordEnvironment()
	logFile, err := os.OpenFile(filepath.Join(directory, "word-times.log"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return result, err
	}
	defer logFile.Close()
	cmd.Stdout, cmd.Stderr = logFile, logFile
	if err = cmd.Run(); err != nil {
		return result, fmt.Errorf("excerpt decoder: %w (see word-times.log)", err)
	}
	raw, err := os.ReadFile(output)
	if err != nil {
		return result, err
	}
	var batch WordTimeObservations
	if err = json.Unmarshal(raw, &batch); err != nil {
		return result, err
	}
	sum := sha256.Sum256(snapshot)
	if batch.InputSHA256 != hex.EncodeToString(sum[:]) {
		return result, fmt.Errorf("word-time snapshot mismatch")
	}
	return ApplyObservedWordTimes(result, batch)
}
