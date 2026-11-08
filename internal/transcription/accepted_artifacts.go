package transcription

import (
	"fmt"
	"os"
	"path/filepath"
	"quill/internal/models"
)

// RefreshAcceptedTranscriptFiles derives sidecars from the accepted DB result.
// It never moves source audio or changes titles, notes, summaries or identities.
func RefreshAcceptedTranscriptFiles(job *models.TranscriptionJob) error {
	if job.Transcript == nil {
		return fmt.Errorf("accepted transcript unavailable")
	}
	if job.TranscriptJSONPath == nil {
		return nil
	}
	path := *job.TranscriptJSONPath
	tmp, err := os.CreateTemp(filepath.Dir(path), ".accepted-*.tmp")
	if err != nil {
		return err
	}
	name := tmp.Name()
	defer os.Remove(name)
	if _, err = tmp.WriteString(*job.Transcript); err != nil {
		tmp.Close()
		return err
	}
	if err = tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err = tmp.Close(); err != nil {
		return err
	}
	if err = os.Rename(name, path); err != nil {
		return err
	}
	if job.TranscriptMarkdownPath != nil {
		return RewriteTranscriptMarkdown(*job.TranscriptMarkdownPath, path, job, nil)
	}
	return nil
}
