package transcription

import (
	"encoding/json"
	"fmt"
	"strings"
	"unicode"

	"quill/internal/transcription/interfaces"
)

const (
	minimumSuspiciousFillerSegments = 3
	minimumFillerOccurrences        = 8
	minimumFillerWordRatio          = 0.05
)

var suspiciousFillerPhrases = [][]string{
	{"thank", "you"},
	{"thanks", "for", "watching"},
	{"please", "subscribe"},
	{"see", "you", "next", "time"},
}

type asrQualityDiagnostics struct {
	Segments []struct {
		Text         string  `json:"text"`
		NoSpeechProb float64 `json:"no_speech_prob"`
	} `json:"segments"`
}

// validateTranscriptQuality rejects only strong, machine-observable ASR
// hallucination signals. It never edits transcript text or guesses what the
// speaker meant; a rejected result leaves the previous transcript untouched.
func validateTranscriptQuality(result *interfaces.TranscriptResult) error {
	if result == nil {
		return fmt.Errorf("transcript quality check received no result")
	}

	if diagnosticsJSON := result.Metadata["asr_diagnostics"]; diagnosticsJSON != "" {
		var diagnostics asrQualityDiagnostics
		if err := json.Unmarshal([]byte(diagnosticsJSON), &diagnostics); err == nil {
			counts := make(map[string]int)
			for _, segment := range diagnostics.Segments {
				phrase, ok := exactSuspiciousFiller(segment.Text)
				if ok && segment.NoSpeechProb >= 0.7 {
					counts[phrase]++
				}
			}
			for phrase, count := range counts {
				if count >= minimumSuspiciousFillerSegments {
					return fmt.Errorf("transcript rejected: likely silence hallucination %q appeared in %d high-no-speech segments", phrase, count)
				}
			}
		}
	}

	words := transcriptQualityWords(result.Text)
	if len(words) == 0 {
		return nil
	}
	for _, phrase := range suspiciousFillerPhrases {
		count := countTokenPhrase(words, phrase)
		fillerWords := count * len(phrase)
		if count >= minimumFillerOccurrences && float64(fillerWords)/float64(len(words)) >= minimumFillerWordRatio {
			return fmt.Errorf("transcript rejected: likely repeated filler hallucination %q appeared %d times", strings.Join(phrase, " "), count)
		}
	}

	return nil
}

func exactSuspiciousFiller(text string) (string, bool) {
	words := transcriptQualityWords(text)
	for _, phrase := range suspiciousFillerPhrases {
		if len(words) != len(phrase) {
			continue
		}
		match := true
		for index := range phrase {
			if words[index] != phrase[index] {
				match = false
				break
			}
		}
		if match {
			return strings.Join(phrase, " "), true
		}
	}
	return "", false
}

func transcriptQualityWords(text string) []string {
	normalized := strings.Map(func(r rune) rune {
		if unicode.IsLetter(r) || unicode.IsNumber(r) || r == '\'' || r == '\u2019' {
			return unicode.ToLower(r)
		}
		return ' '
	}, text)
	return strings.Fields(normalized)
}

func countTokenPhrase(words, phrase []string) int {
	if len(phrase) == 0 || len(words) < len(phrase) {
		return 0
	}
	count := 0
	for start := 0; start <= len(words)-len(phrase); start++ {
		match := true
		for offset := range phrase {
			if words[start+offset] != phrase[offset] {
				match = false
				break
			}
		}
		if match {
			count++
			start += len(phrase) - 1
		}
	}
	return count
}
