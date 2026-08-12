package transcription

import (
	"fmt"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

var (
	archiveTimestampTitlePattern = regexp.MustCompile(`^[\d\s_.:\-]+$`)
	archiveIdentifierPattern     = regexp.MustCompile(`(?i)^20\d{6}[ _-]\d{6}(?:-[0-9a-f]{8,})?$`)
	archiveNewRecordingPattern   = regexp.MustCompile(`(?i)^(?:new recording|neue aufnahme|nueva grabaci[oó]n)(?:\s+\d+)?$`)
	archiveCanonicalTitlePattern = regexp.MustCompile(`^\d{6}\s+\d{2}:?\d{2}\s+`)
)

// GeneratedArchiveTitleInput contains the durable metadata surrounding an
// LLM-generated subject. The subject is only one component of the title.
type GeneratedArchiveTitleInput struct {
	RecordedAt       *time.Time
	CreatedAt        time.Time
	Subject          string
	CurrentTitle     string
	OriginalFilename string
	Speakers         []string
	DurationMillis   int64
}

// FormatGeneratedArchiveTitle builds the canonical, scan-friendly Plume title:
// YYMMDD HH:MM Subject (original title) [Speakers] {MM:SS|HH:MM}.
func FormatGeneratedArchiveTitle(input GeneratedArchiveTitleInput) string {
	recordedAt := input.CreatedAt
	if input.RecordedAt != nil && !input.RecordedAt.IsZero() {
		recordedAt = *input.RecordedAt
	}

	parts := []string{recordedAt.Format("060102 15:04")}
	subject := strings.TrimSpace(input.Subject)
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
	label := ""
	if current != "" && !archiveCanonicalTitlePattern.MatchString(current) {
		label = current
	}
	if label == "" {
		label = strings.TrimSpace(strings.TrimSuffix(filepath.Base(originalFilename), filepath.Ext(originalFilename)))
	}
	if label == "" || strings.EqualFold(label, subject) || strings.EqualFold(label, "Title Not Available") || archiveNewRecordingPattern.MatchString(label) {
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

func formatArchiveDuration(durationMillis int64) string {
	// Match the player/list display, which reports completed whole seconds.
	totalSeconds := durationMillis / 1000
	if totalSeconds >= 3600 {
		return fmt.Sprintf("%02d:%02d", totalSeconds/3600, (totalSeconds%3600)/60)
	}
	return fmt.Sprintf("%02d:%02d", totalSeconds/60, totalSeconds%60)
}
