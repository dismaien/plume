package transcriptedit

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"testing"

	"quill/internal/models"
)

func TestReviewInsideLabelledSegmentPreservesOtherWords(t *testing.T) {
	raw := `{"text":"Open the door.","metadata":{"speaker_review_start":"0.3","some_history":"keep"},"segments":[{"start":0,"end":1,"speaker":"A","text":"Open the door.","custom":"keep"}],"word_segments":[{"word":"Open","start":0,"end":0.3,"speaker":"A","score":0.91},{"word":"the","start":0.3,"end":0.5,"speaker_review":true},{"word":"door.","start":0.5,"end":1,"speaker":"A","score":0.83}]}`
	index := 1
	for _, tc := range []struct {
		speaker, text string
		count         int
	}{{"B", "the", 3}, {"", "the", 3}, {"B", "that blue", 4}} {
		next, err := apply(raw, Request{PassageReview: &PassageReview{Segment: 0, FirstWord: &index, LastWord: &index, Original: "the", Text: tc.text, Speaker: tc.speaker, Cue: true}})
		if err != nil {
			t.Fatal(err)
		}
		var doc struct {
			Text     string
			Segments []reviewItem
			Words    []reviewItem `json:"word_segments"`
			Metadata map[string]string
		}
		if err = json.Unmarshal([]byte(next), &doc); err != nil {
			t.Fatal(err)
		}
		if len(doc.Words) != tc.count || len(doc.Segments) != 3 || valueText(doc.Words[1], "speaker") != tc.speaker || doc.Words[1]["speaker_reviewed"] != true || doc.Words[1]["speaker_review"] != nil || doc.Text != "Open "+tc.text+" door." {
			t.Fatal(next)
		}
		if doc.Words[0]["score"] != .91 || doc.Words[len(doc.Words)-1]["score"] != .83 || doc.Segments[0]["custom"] != "keep" || doc.Metadata["some_history"] != "keep" || doc.Metadata["speaker_review_start"] != "" {
			t.Fatal(next)
		}
	}
}
func TestReviewLegacySegmentAndRejectStaleText(t *testing.T) {
	raw := `{"text":"Yes.","segments":[{"start":1,"end":2,"text":"Yes."}],"manually_edited":true}`
	r := PassageReview{Segment: 0, Original: "Yes.", Text: "Yes."}
	s, err := applyPassage(raw, r)
	if err != nil {
		t.Fatal(err)
	}
	var doc struct{ Segments []reviewItem }
	if json.Unmarshal([]byte(s), &doc) != nil || doc.Segments[0]["speaker_reviewed"] != true {
		t.Fatal(s)
	}
	r.Original = "Other."
	if _, err := applyPassage(raw, r); err == nil {
		t.Fatal("accepted stale content")
	}
}

func TestReviewNeverMovesCorrectionToAnotherReply(t *testing.T) {
	for _, tc := range []struct{ name, full, section string }{
		{"edited full text", "No. Yeah.", "Yeah. Yeah."},
		{"edited segment", "No. Yeah.", "No. Yeah."},
	} {
		t.Run(tc.name, func(t *testing.T) {
			payload := reviewItem{
				"text":     tc.full,
				"segments": []reviewItem{{"start": 1, "end": 3, "text": tc.section}},
				"word_segments": []reviewItem{
					{"start": 1, "end": 2, "word": "Yeah."},
					{"start": 2, "end": 3, "word": "Yeah.", "speaker": "B"},
				},
			}
			raw, err := json.Marshal(payload)
			if err != nil {
				t.Fatal(err)
			}
			s, _ := setup(t)
			if err := s.DB.Model(&models.TranscriptionJob{}).Where("id = ?", "one").Update("transcript", string(raw)).Error; err != nil {
				t.Fatal(err)
			}
			before := current(t, s, "one")
			first := 0
			_, err = s.Save(context.Background(), "one", Request{ExpectedVersion: before.Version, PassageReview: &PassageReview{
				Segment: 0, FirstWord: &first, LastWord: &first, Original: "Yeah.", Text: "Yes.", Speaker: "A",
			}})
			if !errors.Is(err, ErrConflict) {
				t.Fatalf("expected a conflict, got %v", err)
			}
			after := current(t, s, "one")
			if after.Version != before.Version || string(after.Transcript) != string(before.Transcript) {
				t.Fatal("review changed the saved transcript")
			}
		})
	}
}

func TestReviewOverlappingRepliesStayWithTheirOwnSegment(t *testing.T) {
	raw := `{"text":"Yeah. Yeah.","segments":[{"start":0,"end":1.5,"text":"Yeah.","speaker":"A"},{"start":1,"end":2,"text":"Yeah."}],"word_segments":[{"start":1,"end":1.4,"word":"Yeah.","speaker":"A"},{"start":1.4,"end":1.8,"word":"Yeah."}]}`
	second := 1
	r := PassageReview{Segment: 0, FirstWord: &second, LastWord: &second, Original: "Yeah.", Text: "Yes.", Speaker: "B"}
	if _, err := applyPassage(raw, r); !errors.Is(err, ErrConflict) {
		t.Fatalf("accepted the neighboring segment: %v", err)
	}
	r.Segment = 1
	next, err := applyPassage(raw, r)
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Text     string
		Segments []reviewItem
	}
	if err := json.Unmarshal([]byte(next), &doc); err != nil {
		t.Fatal(err)
	}
	if doc.Text != "Yeah. Yes." || len(doc.Segments) != 2 || doc.Segments[0]["speaker"] != "A" || doc.Segments[1]["speaker"] != "B" {
		t.Fatal(next)
	}
}

func TestWordingCorrectionPreservesRepliesAndSpeakerReview(t *testing.T) {
	raw := `{"text":"Ja gut. Yeah. Ja.","segments":[{"start":0,"end":4,"text":"Ja gut. Yeah. Ja.","speaker":"A"}],"word_segments":[{"word":"Ja","start":0,"end":0.4,"speaker":"A","score":0.99},{"word":"gut.","start":0.5,"end":1,"speaker":"A"},{"word":"Yeah.","start":1.1,"end":1.4,"speaker":"B","speaker_reviewed":true},{"word":"Ja.","start":1.5,"end":2,"speaker":"A","speaker_review":true}]}`
	a, b := 0, 1
	request := PassageReview{WordingOnly: true, Segment: 0, FirstWord: &a, LastWord: &b, Original: "Ja gut.", Text: "Ja sehr gut."}
	updated, err := applyPassage(raw, request)
	if err != nil {
		t.Fatal(err)
	}
	type snapshot struct {
		Text     string
		Words    []reviewItem `json:"word_segments"`
		Segments []reviewItem
	}
	var before, after snapshot
	if err = json.Unmarshal([]byte(raw), &before); err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal([]byte(updated), &after); err != nil {
		t.Fatal(err)
	}
	if after.Text != "Ja sehr gut. Yeah. Ja." || len(after.Segments) != 1 || after.Segments[0]["speaker"] != "A" ||
		!reflect.DeepEqual(after.Words[0], before.Words[0]) || !reflect.DeepEqual(after.Words[2:], before.Words[1:]) ||
		after.Words[1]["speaker"] != "A" || after.Words[1]["speaker_reviewed"] != nil {
		t.Fatalf("Unexpected correction: %s", updated)
	}
	b = 2
	request.Original = "Ja gut. Yeah."
	if _, err = applyPassage(raw, request); !errors.Is(err, ErrConflict) {
		t.Fatalf("Expected conflict at speaker boundary, got %v", err)
	}
}
