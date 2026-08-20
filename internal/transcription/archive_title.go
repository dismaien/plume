package transcription

import (
	"fmt"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

var (
	archiveTimestampTitlePattern    = regexp.MustCompile(`^[\d\s_.:\-]+$`)
	archiveIdentifierPattern        = regexp.MustCompile(`(?i)^20\d{6}[ _-]\d{6}(?:-[0-9a-f]{8,})?$`)
	archiveNewRecordingPattern      = regexp.MustCompile(`(?i)^(?:new recording|neue aufnahme|nueva grabaci[oó]n)(?:\s+\d+)?$`)
	archiveCanonicalTitlePattern    = regexp.MustCompile(`^\d{6}\s+\d{2}:?\d{2}\s+`)
	archiveDreamWordPattern         = regexp.MustCompile(`(?i)(?:^|[^[:alnum:]])dreams?(?:$|[^[:alnum:]])`)
	archiveDreamSubjectPattern      = regexp.MustCompile(`(?i)^dreams?(?:$|\s|[:\-])|\sdream$`)
	archiveDreamMusicContextPattern = regexp.MustCompile(`(?i)\b(?:song|singing|lyrics?|music|dreamscape)\b`)
	archiveDreamMarkerPattern       = regexp.MustCompile(`(?i)\s*\(dream\)\s*`)
	archiveDreamTranscriptPattern   = regexp.MustCompile(
		`(?i)\b(?:in|from|about|remember(?:ing)?|recall(?:ing)?|had|having|last image in)\s+(?:a\s+|the\s+|my\s+|this\s+|that\s+)?dream\b|\bi\s+(?:dreamed|dreamt)\b|\bdream\s+i\b`,
	)
	archiveDreamSubjectPrefixPattern = regexp.MustCompile(`(?i)^dream(?: account| note| recording)?\s*[:\-]\s*`)
	archiveDreamSubjectSuffixPattern = regexp.MustCompile(`(?i)\s+dream$`)
)

// GeneratedArchiveTitleInput contains the durable metadata surrounding an
// LLM-generated subject. The subject is only one component of the title.
type GeneratedArchiveTitleInput struct {
	RecordedAt       *time.Time
	CreatedAt        time.Time
	Subject          string
	CurrentTitle     string
	OriginalFilename string
	Transcript       string
	Speakers         []string
	DurationMillis   int64
}

// FormatGeneratedArchiveTitle builds the canonical, scan-friendly Plume title:
// YYMMDD HH:MM (dream) Subject (original title) [Speakers] {MM:SS|HH:MM}.
func FormatGeneratedArchiveTitle(input GeneratedArchiveTitleInput) string {
	recordedAt := input.CreatedAt
	if input.RecordedAt != nil && !input.RecordedAt.IsZero() {
		recordedAt = *input.RecordedAt
	}

	parts := []string{recordedAt.Format("060102 15:04")}
	isDream := isLikelyDreamArchiveRecording(input)
	if isDream {
		parts = append(parts, "(dream)")
	}

	subject := cleanGeneratedDreamSubject(input.Subject, isDream)
	if subject != "" {
		parts = append(parts, subject)
	}

	if original := sourceTitleLabel(input.CurrentTitle, input.OriginalFilename, subject); original != "" {
		parts = append(parts, "("+original+")")
	}

	if speakers := normalizeSpeakerTitleNames(input.Speakers); len(speakers) > 0 {
		parts = append(parts, "["+strings.Join(speakers, ", ")+"]")
	}

	if input.DurationMillis > 0 {
		parts = append(parts, "{"+formatArchiveDuration(input.DurationMillis)+"}")
	}

	return strings.Join(parts, " ")
}

func sourceTitleLabel(currentTitle string, originalFilename string, subject string) string {
	current := strings.TrimSpace(currentTitle)
	current = strings.TrimSpace(archiveDreamMarkerPattern.ReplaceAllString(current, " "))
	label := ""
	if current != "" && !archiveCanonicalTitlePattern.MatchString(current) {
		label = current
	}
	if label == "" && strings.TrimSpace(originalFilename) != "" {
		label = strings.TrimSpace(strings.TrimSuffix(filepath.Base(originalFilename), filepath.Ext(originalFilename)))
	}
	if label == "" || strings.EqualFold(label, subject) || strings.EqualFold(label, "Title Not Available") || archiveNewRecordingPattern.MatchString(label) {
		return ""
	}
	if strings.EqualFold(label, "dream") || strings.EqualFold(label, "dreams") {
		return ""
	}
	if archiveIdentifierPattern.MatchString(label) {
		return ""
	}
	if archiveTimestampTitlePattern.MatchString(label) {
		digits := 0
		for _, char := range label {
			if char >= '0' && char <= '9' {
				digits++
			}
		}
		if digits >= 8 {
			return ""
		}
	}
	if strings.HasPrefix(label, "Multi-track Job ") || strings.EqualFold(label, "YouTube Audio") {
		return ""
	}
	if len(label) > 80 {
		label = strings.TrimSpace(label[:77]) + "..."
	}
	return label
}

func isLikelyDreamArchiveRecording(input GeneratedArchiveTitleInput) bool {
	currentTitle := strings.TrimSpace(input.CurrentTitle)
	if archiveDreamMarkerPattern.MatchString(currentTitle) {
		return true
	}
	// A non-canonical current title is source metadata. Once a canonical title
	// exists, require its explicit marker so phrases such as "dream home" do not
	// become sticky false positives during later regeneration.
	if !archiveCanonicalTitlePattern.MatchString(currentTitle) &&
		archiveDreamWordPattern.MatchString(currentTitle) &&
		!archiveDreamMusicContextPattern.MatchString(currentTitle) {
		return true
	}
	if archiveDreamWordPattern.MatchString(input.OriginalFilename) {
		return true
	}
	if archiveDreamSubjectPattern.MatchString(strings.TrimSpace(input.Subject)) &&
		!archiveDreamMusicContextPattern.MatchString(input.Subject) {
		return true
	}

	transcript := strings.TrimSpace(input.Transcript)
	if len(transcript) > 1600 {
		transcript = transcript[:1600]
	}
	return archiveDreamTranscriptPattern.MatchString(transcript)
}

func cleanGeneratedDreamSubject(subject string, isDream bool) string {
	cleaned := strings.TrimSpace(subject)
	if !isDream || cleaned == "" {
		return cleaned
	}
	cleaned = strings.TrimSpace(archiveDreamMarkerPattern.ReplaceAllString(cleaned, " "))
	cleaned = strings.TrimSpace(archiveDreamSubjectPrefixPattern.ReplaceAllString(cleaned, ""))
	if withoutSuffix := strings.TrimSpace(archiveDreamSubjectSuffixPattern.ReplaceAllString(cleaned, "")); withoutSuffix != "" {
		cleaned = withoutSuffix
	}
	return cleaned
}

func formatArchiveDuration(durationMillis int64) string {
	// Match the player/list display, which rounds to the nearest whole second.
	totalSeconds := (durationMillis + 500) / 1000
	if totalSeconds >= 3600 {
		return fmt.Sprintf("%02d:%02d", totalSeconds/3600, (totalSeconds%3600)/60)
	}
	return fmt.Sprintf("%02d:%02d", totalSeconds/60, totalSeconds%60)
}
