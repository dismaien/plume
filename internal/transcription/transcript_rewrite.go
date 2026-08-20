package transcription

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"quill/internal/models"
)

// RewriteTranscriptJSON reads the transcript JSON at jsonPath, adds or updates
// the "speaker_name" field in each segment based on the provided mappings
// (key = original_speaker, value = custom display name), and writes back
// atomically using a temp-file rename.
//
// The "speaker" field is never modified; it remains the stable original key
// (e.g. "speaker_00"). If a segment has no "speaker" field, or the speaker
// value has no entry in mappings, no "speaker_name" field is set/updated.
func RewriteTranscriptJSON(jsonPath string, mappings map[string]string) error {
	raw, err := os.ReadFile(jsonPath)
	if err != nil {
		return fmt.Errorf("transcript_rewrite: read %s: %w", jsonPath, err)
	}

	out, err := rewriteTranscriptJSON(raw, mappings, true)
	if err != nil {
		return fmt.Errorf("transcript_rewrite: parse %s: %w", jsonPath, err)
	}

	// Atomic write: write to a sibling temp file, then rename.
	dir := filepath.Dir(jsonPath)
	tmp, err := os.CreateTemp(dir, ".transcript_rewrite_*.json.tmp")
	if err != nil {
		return fmt.Errorf("transcript_rewrite: create temp file: %w", err)
	}
	tmpName := tmp.Name()
	defer func() {
		// Clean up temp file on any error path (rename will have removed it on success).
		_ = os.Remove(tmpName)
	}()

	if _, err := tmp.Write(out); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("transcript_rewrite: write temp file: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("transcript_rewrite: close temp file: %w", err)
	}

	if err := os.Rename(tmpName, jsonPath); err != nil {
		return fmt.Errorf("transcript_rewrite: rename temp to %s: %w", jsonPath, err)
	}

	return nil
}

// RewriteTranscriptString applies speaker display names to the transcript JSON
// stored in the database. Raw diarization labels remain intact so a rename can
// always be reversed or changed later.
func RewriteTranscriptString(raw string, mappings map[string]string) (string, error) {
	out, err := rewriteTranscriptJSON([]byte(raw), mappings, false)
	if err != nil {
		return "", err
	}
	return string(out), nil
}

func rewriteTranscriptJSON(raw []byte, mappings map[string]string, pretty bool) ([]byte, error) {
	var payload map[string]interface{}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil, err
	}

	// Word-level speaker identity is used by downloads and playback tooling;
	// segment identity is used by the transcript views.
	applySpeakerNames(payload["segments"], mappings)
	applySpeakerNames(payload["word_segments"], mappings)

	if pretty {
		return json.MarshalIndent(payload, "", "  ")
	}
	return json.Marshal(payload)
}

func applySpeakerNames(rawItems interface{}, mappings map[string]string) {
	items, ok := rawItems.([]interface{})
	if !ok {
		return
	}
	for _, rawItem := range items {
		item, ok := rawItem.(map[string]interface{})
		if !ok {
			continue
		}
		speaker, ok := item["speaker"].(string)
		if !ok {
			continue
		}
		if displayName, mapped := mappings[speaker]; mapped {
			item["speaker_name"] = displayName
		} else {
			delete(item, "speaker_name")
		}
	}
}

// ConfirmedSpeakerNameMap builds the original-label to display-name lookup
// shared by transcript sidecars and the database transcript.
func ConfirmedSpeakerNameMap(speakerMappings []models.SpeakerMapping) map[string]string {
	mappings := make(map[string]string, len(speakerMappings))
	canonicalNames := make(map[string]string, len(speakerMappings))
	for _, mapping := range speakerMappings {
		name := strings.TrimSpace(mapping.CustomName)
		if !mapping.IsConfirmedIdentity() || name == "" {
			continue
		}
		identityKey := strings.ToLower(name)
		if canonical, exists := canonicalNames[identityKey]; exists {
			name = canonical
		} else {
			canonicalNames[identityKey] = name
		}
		mappings[mapping.OriginalSpeaker] = name
	}
	return mappings
}

// transcriptSegmentForRewrite is the segment representation used when
// reading JSON to regenerate the markdown file.
type transcriptSegmentForRewrite struct {
	Start       float64 `json:"start"`
	End         float64 `json:"end"`
	Text        string  `json:"text"`
	Speaker     *string `json:"speaker,omitempty"`
	SpeakerName *string `json:"speaker_name,omitempty"`
}

// transcriptPayloadForRewrite is the top-level JSON structure used when
// regenerating the markdown file.
type transcriptPayloadForRewrite struct {
	Text     string                        `json:"text"`
	Segments []transcriptSegmentForRewrite `json:"segments,omitempty"`
}

// RewriteTranscriptMarkdown regenerates the markdown transcript file at mdPath
// by reading the JSON at jsonPath and applying the mappings (original_speaker →
// custom_name). Display name resolution order:
//  1. The "speaker_name" field already set in the JSON (written by RewriteTranscriptJSON).
//  2. The mappings parameter (allows callers to pass the latest mappings before
//     the JSON file is updated).
//  3. The raw "speaker" field as fallback.
func RewriteTranscriptMarkdown(mdPath string, jsonPath string, job *models.TranscriptionJob, mappings map[string]string) error {
	raw, err := os.ReadFile(jsonPath)
	if err != nil {
		return fmt.Errorf("transcript_rewrite: read json %s: %w", jsonPath, err)
	}

	var payload transcriptPayloadForRewrite
	if err := json.Unmarshal(raw, &payload); err != nil {
		return fmt.Errorf("transcript_rewrite: parse json %s: %w", jsonPath, err)
	}

	// Convert to the markdownTranscriptPayload used by renderMarkdownTranscript,
	// applying display-name resolution.
	mdPayload := &markdownTranscriptPayload{
		Text: payload.Text,
	}
	for _, seg := range payload.Segments {
		displayName := resolveDisplayName(seg.Speaker, seg.SpeakerName, mappings)
		mdSeg := markdownTranscriptSegment{
			Start:   seg.Start,
			End:     seg.End,
			Text:    seg.Text,
			Speaker: displayName,
		}
		mdPayload.Segments = append(mdPayload.Segments, mdSeg)
	}

	markdown := renderMarkdownTranscript(job, mdPayload, nil)

	// Atomic write.
	dir := filepath.Dir(mdPath)
	tmp, err := os.CreateTemp(dir, ".transcript_rewrite_*.md.tmp")
	if err != nil {
		return fmt.Errorf("transcript_rewrite: create temp md file: %w", err)
	}
	tmpName := tmp.Name()
	defer func() { _ = os.Remove(tmpName) }()

	if _, err := tmp.WriteString(markdown); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("transcript_rewrite: write temp md file: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("transcript_rewrite: close temp md file: %w", err)
	}
	if err := os.Rename(tmpName, mdPath); err != nil {
		return fmt.Errorf("transcript_rewrite: rename temp md to %s: %w", mdPath, err)
	}

	return nil
}

// resolveDisplayName picks the best display name for a segment speaker.
// Priority: confirmed mappings lookup > speaker_name field > raw speaker key.
func resolveDisplayName(speaker *string, speakerName *string, mappings map[string]string) *string {
	if speaker != nil {
		if display, ok := mappings[*speaker]; ok && display != "" {
			return &display
		}
	}
	if speakerName != nil && strings.TrimSpace(*speakerName) != "" {
		return speakerName
	}
	if speaker != nil {
		// Fallback to raw speaker key.
		return speaker
	}
	return nil
}

// RewriteTranscriptFiles is the orchestrator that rewrites both the JSON and
// the markdown transcript files for a job after speaker mappings have been
// updated.
//
// It requires that both TranscriptJSONPath and TranscriptMarkdownPath are set
// on the job; if either is nil an error is returned.
func RewriteTranscriptFiles(job *models.TranscriptionJob, speakerMappings []models.SpeakerMapping) error {
	if job.TranscriptJSONPath == nil {
		return fmt.Errorf("transcript_rewrite: TranscriptJSONPath is nil for job %s", job.ID)
	}
	if job.TranscriptMarkdownPath == nil {
		return fmt.Errorf("transcript_rewrite: TranscriptMarkdownPath is nil for job %s", job.ID)
	}

	mappings := ConfirmedSpeakerNameMap(speakerMappings)

	// Stamp UpdatedAt so the markdown front-matter stays current.
	job.UpdatedAt = time.Now()

	if err := RewriteTranscriptJSON(*job.TranscriptJSONPath, mappings); err != nil {
		return err
	}

	if err := RewriteTranscriptMarkdown(
		*job.TranscriptMarkdownPath,
		*job.TranscriptJSONPath,
		job,
		mappings,
	); err != nil {
		return err
	}

	return nil
}
