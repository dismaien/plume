package adapters

import (
	"encoding/json"
	"fmt"
	"quill/internal/transcription/interfaces"
	"strings"
)

// Full-audio ASR is written before alignment starts. A later alignment/output
// failure must not destroy already decoded speech. Decoder word timestamps are
// retained with an explicit failed alignment stage; they are not forced alignment.
func recoverFullAudioTranscript(data []byte) (*interfaces.TranscriptResult, error) {
	var p struct {
		Decoder  string `json:"decoder"`
		Language string `json:"language"`
		Segments []struct {
			Start float64 `json:"start"`
			End   float64 `json:"end"`
			Text  string  `json:"text"`
			Words []struct {
				Start       float64 `json:"start"`
				End         float64 `json:"end"`
				Word        string  `json:"word"`
				Probability float64 `json:"probability"`
			} `json:"words"`
		} `json:"segments"`
	}
	if err := json.Unmarshal(data, &p); err != nil {
		return nil, err
	}
	if p.Decoder != "faster_whisper_full_audio" || p.Segments == nil {
		return nil, fmt.Errorf("no complete ASR recovery evidence")
	}
	result := &interfaces.TranscriptResult{Language: p.Language, Metadata: map[string]string{"alignment_status": "failed", "alignment_reason": "post_asr_stage_failed", "timestamp_source": "decoder"}}
	var text strings.Builder
	for _, segment := range p.Segments {
		text.WriteString(segment.Text)
		result.Segments = append(result.Segments, interfaces.TranscriptSegment{Start: segment.Start, End: segment.End, Text: segment.Text})
		for _, w := range segment.Words {
			result.WordSegments = append(result.WordSegments, interfaces.TranscriptWord{Start: w.Start, End: w.End, Word: w.Word, Score: w.Probability})
		}
	}
	result.Text = text.String()
	return result, nil
}
