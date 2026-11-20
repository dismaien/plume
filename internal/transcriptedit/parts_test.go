package transcriptedit

import (
	"context"
	"encoding/json"
	"errors"
	"quill/internal/models"
	"testing"
)

func TestSpeakerPhraseSplitHistoryAndRestore(t *testing.T) {
	s, _ := setup(t)
	ctx := context.Background()
	raw := `{"text":"é Other words. It's like my safety in the world is limited.","custom":42,"segments":[{"start":0,"end":10,"speaker":"A","speaker_name":"Alice","text":"é Other words. It's like my safety in the world is ","words":[{"word":"Other","speaker":"A"}]},{"start":10,"end":12,"speaker":"B","speaker_name":"Brett","text":"limited."}],"word_segments":[{"word":"Other","speaker":"A"}]}`
	s.DB.Model(&models.TranscriptionJob{}).Where("id = ?", "one").Update("transcript", raw)
	base := current(t, s, "one")
	req := Request{ExpectedVersion: base.Version, SegmentParts: [][]SegmentPart{{{Text: "é Other words. ", Speaker: "A"}, {Text: "It's like my safety in the world is ", Speaker: "B"}}, {{Text: "limited.", Speaker: "B"}}}}
	next, err := s.Save(ctx, "one", req)
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Custom   int
		Text     string
		Segments []struct {
			Text, Speaker, SpeakerName string
			Start, End                 float64
			Approximate                bool `json:"timing_approximate"`
		}
	}
	// Inspect names explicitly because stored field uses an underscore.
	var payload map[string]json.RawMessage
	json.Unmarshal(next.Transcript, &payload)
	json.Unmarshal(next.Transcript, &doc)
	if doc.Custom != 42 || len(doc.Segments) != 3 || doc.Segments[1].Speaker != "B" || doc.Segments[0].Speaker != "A" {
		t.Fatal(string(next.Transcript))
	}
	if doc.Segments[0].Text+doc.Segments[1].Text != "é Other words. It's like my safety in the world is " {
		t.Fatal("split lost text")
	}
	if doc.Segments[0].Start != 0 || doc.Segments[0].End != doc.Segments[1].Start || doc.Segments[1].End != 10 || !doc.Segments[1].Approximate {
		t.Fatal("bad split times", doc)
	}
	var segments []map[string]any
	json.Unmarshal(payload["segments"], &segments)
	if segments[1]["speaker_name"] != "Brett" || segments[0]["words"] != nil || payload["word_segments"] != nil {
		t.Fatal("stale attribution", string(next.Transcript))
	}
	rows, _ := s.History(ctx, "one", 0)
	if len(rows) != 2 || rows[0].Kind != "speaker_edit" {
		t.Fatal(rows)
	}
	if _, err = s.Save(ctx, "one", req); !errors.Is(err, ErrConflict) {
		t.Fatal("stale speaker edit accepted", err)
	}
	id := rows[1].ID
	restored, err := s.Save(ctx, "one", Request{ExpectedVersion: next.Version, RestoreID: &id})
	if err != nil || string(restored.Transcript) != raw {
		t.Fatal("restore not exact", err)
	}
}

func TestInvalidSpeakerPartsDoNotWriteHistory(t *testing.T) {
	s, _ := setup(t)
	base := current(t, s, "one")
	for _, parts := range [][][]SegmentPart{
		{}, {{{Text: "wrong words", Speaker: "unknown"}}},
	} {
		_, err := s.Save(context.Background(), "one", Request{ExpectedVersion: base.Version, SegmentParts: parts})
		if !errors.Is(err, ErrInvalid) {
			t.Fatal("invalid split accepted", parts, err)
		}
	}
	same, err := s.Save(context.Background(), "one", Request{ExpectedVersion: base.Version, SegmentParts: [][]SegmentPart{{{Text: "wrong words", Speaker: "SPEAKER_00"}}}})
	if err != nil || string(same.Transcript) != original || same.Version != base.Version {
		t.Fatal("no-op changed transcript", err)
	}
	rows, _ := s.History(context.Background(), "one", 0)
	if len(rows) != 0 {
		t.Fatal("invalid/no-op history", rows)
	}
}

func TestEmptySpeakerSectionIsRemovedWithoutLosingMovedText(t *testing.T) {
	s, _ := setup(t)
	raw := `{"text":"One thing that happens","custom":42,"segments":[{"start":0,"end":1,"speaker":"","text":"One thing that","words":[{"word":"One"}]},{"start":1,"end":2,"speaker":"A","speaker_name":"Abraham","text":" happens"}],"word_segments":[{"word":"One"}]}`
	s.DB.Model(&models.TranscriptionJob{}).Where("id = ?", "one").Update("transcript", raw)
	base := current(t, s, "one")
	next, err := s.Save(context.Background(), "one", Request{ExpectedVersion: base.Version, SegmentParts: [][]SegmentPart{
		{},
		{{Text: "One thing that happens", Speaker: "A"}},
	}})
	if err != nil {
		t.Fatal(err)
	}
	var got struct {
		Text     string `json:"text"`
		Custom   int    `json:"custom"`
		Segments []struct {
			Text    string  `json:"text"`
			Speaker string  `json:"speaker"`
			Start   float64 `json:"start"`
			End     float64 `json:"end"`
		} `json:"segments"`
	}
	if err := json.Unmarshal(next.Transcript, &got); err != nil {
		t.Fatal(err)
	}
	if got.Text != "One thing that happens" || got.Custom != 42 || len(got.Segments) != 1 || got.Segments[0].Text != got.Text || got.Segments[0].Speaker != "A" || got.Segments[0].Start != 1 || got.Segments[0].End != 2 {
		t.Fatal(string(next.Transcript))
	}
	rows, err := s.History(context.Background(), "one", 0)
	if err != nil || len(rows) != 2 {
		t.Fatal(rows, err)
	}
	restored, err := s.Save(context.Background(), "one", Request{ExpectedVersion: next.Version, RestoreID: &rows[1].ID})
	if err != nil || string(restored.Transcript) != raw {
		t.Fatal("original was not restorable", err)
	}
}

func TestBlankSplitPartIsPruned(t *testing.T) {
	s, _ := setup(t)
	base := current(t, s, "one")
	next, err := s.Save(context.Background(), "one", Request{ExpectedVersion: base.Version, SegmentParts: [][]SegmentPart{{
		{Text: " ", Speaker: "SPEAKER_00"},
		{Text: "correct words", Speaker: "SPEAKER_00"},
	}}})
	if err != nil {
		t.Fatal(err)
	}
	var payload struct {
		Text     string `json:"text"`
		Segments []struct {
			Text string `json:"text"`
		} `json:"segments"`
	}
	if err := json.Unmarshal(next.Transcript, &payload); err != nil || payload.Text != "correct words" || len(payload.Segments) != 1 || payload.Segments[0].Text != "correct words" {
		t.Fatal(string(next.Transcript), err)
	}
}

func TestAllBlankSpeakerPartsLeaveEmptyTranscript(t *testing.T) {
	s, _ := setup(t)
	base := current(t, s, "one")
	next, err := s.Save(context.Background(), "one", Request{ExpectedVersion: base.Version, SegmentParts: [][]SegmentPart{{}}})
	if err != nil {
		t.Fatal(err)
	}
	var payload struct {
		Text     string            `json:"text"`
		Segments []json.RawMessage `json:"segments"`
	}
	if err := json.Unmarshal(next.Transcript, &payload); err != nil || payload.Text != "" || len(payload.Segments) != 0 {
		t.Fatal(string(next.Transcript), err)
	}
}
