package transcriptedit

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"testing"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
	"quill/internal/models"
)

const original = `{"text":"wrong words","language":"en","custom":{"keep":true},"segments":[{"start":1,"end":4,"text":"wrong words","speaker":"SPEAKER_00","words":[{"word":"wrong","start":1}],"tokens":[1]}],"word_segments":[{"word":"wrong","start":1}]}`

func setup(t *testing.T) (Store, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "test.db")
	db, err := gorm.Open(sqlite.Open(path), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&models.TranscriptionJob{}, &models.TranscriptRevision{}); err != nil {
		t.Fatal(err)
	}
	raw := original
	for _, id := range []string{"one", "two"} {
		if err := db.Create(&models.TranscriptionJob{ID: id, AudioPath: "untouched.wav", Status: models.StatusCompleted, Transcript: &raw}).Error; err != nil {
			t.Fatal(err)
		}
	}
	return Store{db}, path
}
func current(t *testing.T, s Store, id string) Document {
	t.Helper()
	d, e := s.Current(context.Background(), id)
	if e != nil {
		t.Fatal(e)
	}
	return d
}
func edit(t *testing.T, s Store, base Document, text string) Document {
	t.Helper()
	d, e := s.Save(context.Background(), "one", Request{ExpectedVersion: base.Version, SegmentTexts: []string{text}})
	if e != nil {
		t.Fatal(e)
	}
	return d
}
func TestHistorySurvivesRestartRestoreAndRetranscription(t *testing.T) {
	s, path := setup(t)
	ctx := context.Background()
	base := current(t, s, "one")
	next := edit(t, s, base, "Corrected words.")
	var payload map[string]any
	if json.Unmarshal(next.Transcript, &payload) != nil {
		t.Fatal("bad output")
	}
	if payload["text"] != "Corrected words." || payload["custom"] == nil || payload["language"] != "en" {
		t.Fatal(payload)
	}
	if _, ok := payload["word_segments"]; ok {
		t.Fatal("stale word alignment")
	}
	seg := payload["segments"].([]any)[0].(map[string]any)
	if seg["speaker"] != "SPEAKER_00" || seg["start"] != float64(1) || seg["end"] != float64(4) || seg["words"] != nil || seg["tokens"] != nil {
		t.Fatal(seg)
	}
	rows, e := s.History(ctx, "one", 0)
	if e != nil || len(rows) != 2 {
		t.Fatal(rows, e)
	}
	baseline, e := s.Revision(ctx, "one", rows[1].ID)
	if e != nil || baseline.Transcript != original {
		t.Fatal("original not exact", e)
	}
	db, e := gorm.Open(sqlite.Open(path), &gorm.Config{})
	if e != nil {
		t.Fatal(e)
	}
	s = Store{db}
	again := current(t, s, "one")
	if again.Version != next.Version {
		t.Fatal("not durable")
	}
	// A later ASR result replaces current text, but saved corrections remain recoverable.
	machine := `{"text":"a new decode"}`
	if e = s.DB.Model(&models.TranscriptionJob{}).Where("id = ?", "one").Update("transcript", machine).Error; e != nil {
		t.Fatal(e)
	}
	if _, e = s.Save(ctx, "one", Request{ExpectedVersion: again.Version, SegmentTexts: []string{"stale"}}); !errors.Is(e, ErrConflict) {
		t.Fatal("stale save accepted", e)
	}
	now := current(t, s, "one")
	restoreID := rows[0].ID
	restored, e := s.Save(ctx, "one", Request{ExpectedVersion: now.Version, RestoreID: &restoreID})
	if e != nil || string(restored.Transcript) != string(next.Transcript) {
		t.Fatal(restored, e)
	}
	rows, e = s.History(ctx, "one", 0)
	if e != nil || len(rows) != 4 || rows[0].Kind != "restore" || rows[1].Kind != "before_edit" {
		t.Fatal(rows, e)
	}
	r, e := s.Revision(ctx, "one", rows[1].ID)
	if e != nil || r.Transcript != machine {
		t.Fatal("intervening ASR lost")
	}
	if _, e = s.Save(ctx, "two", Request{ExpectedVersion: current(t, s, "two").Version, RestoreID: &restoreID}); !errors.Is(e, gorm.ErrRecordNotFound) {
		t.Fatal("cross-job restore accepted", e)
	}
}
func TestAtomicFailureNoopAndInvalidRequests(t *testing.T) {
	s, _ := setup(t)
	ctx := context.Background()
	base := current(t, s, "one")
	noop := edit(t, s, base, "wrong words")
	if noop.Version != base.Version || string(noop.Transcript) != original {
		t.Fatal("noop changed content")
	}
	rows, _ := s.History(ctx, "one", 0)
	if len(rows) != 0 {
		t.Fatal("noop revision")
	}
	if _, e := s.Save(ctx, "one", Request{ExpectedVersion: base.Version, SegmentTexts: []string{"a", "b"}}); !errors.Is(e, ErrInvalid) {
		t.Fatal("invalid shape accepted")
	}
	if e := s.DB.Exec(`CREATE TRIGGER fail_revision BEFORE INSERT ON transcript_revisions BEGIN SELECT RAISE(ABORT,'disk failure'); END`).Error; e != nil {
		t.Fatal(e)
	}
	if _, e := s.Save(ctx, "one", Request{ExpectedVersion: base.Version, SegmentTexts: []string{"changed"}}); e == nil {
		t.Fatal("expected failure")
	}
	if got := current(t, s, "one"); got.Version != base.Version || string(got.Transcript) != original {
		t.Fatal("partial commit")
	}
	s.DB.Exec("DROP TRIGGER fail_revision")
	s.DB.Model(&models.TranscriptionJob{}).Where("id = ?", "one").Update("status", models.StatusProcessing)
	if _, e := s.Save(ctx, "one", Request{ExpectedVersion: base.Version, SegmentTexts: []string{"changed"}}); !errors.Is(e, ErrUnavailable) {
		t.Fatal("edited processing recording")
	}
}
func TestPlainTextAndEmptyCorrection(t *testing.T) {
	s, _ := setup(t)
	ctx := context.Background()
	for _, raw := range []string{`"hello"`, `{"text":"hello","language":"de"}`} {
		s.DB.Model(&models.TranscriptionJob{}).Where("id = ?", "one").Update("transcript", raw)
		text := "Corrected\nparagraph."
		d, e := s.Save(ctx, "one", Request{ExpectedVersion: current(t, s, "one").Version, Text: &text})
		if e != nil {
			t.Fatal(e)
		}
		text = ""
		if _, e = s.Save(ctx, "one", Request{ExpectedVersion: d.Version, Text: &text}); e != nil {
			t.Fatal(e)
		}
	}
}

func TestSegmentTextsPrunesEmptySection(t *testing.T) {
	s, _ := setup(t)
	raw := `{"text":"Hello there","segments":[{"start":0,"end":1,"text":"Hello","speaker":"A"},{"start":1,"end":2,"text":" there","speaker":""}]}`
	s.DB.Model(&models.TranscriptionJob{}).Where("id = ?", "one").Update("transcript", raw)
	base := current(t, s, "one")
	next, err := s.Save(context.Background(), "one", Request{ExpectedVersion: base.Version, SegmentTexts: []string{"Hello there", ""}})
	if err != nil {
		t.Fatal(err)
	}
	var got struct {
		Text     string `json:"text"`
		Segments []struct {
			Text string `json:"text"`
		} `json:"segments"`
	}
	if err := json.Unmarshal(next.Transcript, &got); err != nil || got.Text != "Hello there" || len(got.Segments) != 1 || got.Segments[0].Text != "Hello there" {
		t.Fatal(string(next.Transcript), err)
	}
}

func TestSearchPaginationAndStaleManualSave(t *testing.T) {
	s, _ := setup(t)
	ctx := context.Background()
	if err := s.DB.Exec(`CREATE VIRTUAL TABLE transcription_fts USING fts5(job_id,title,content,summary)`).Error; err != nil {
		t.Fatal(err)
	}
	base := current(t, s, "one")
	_ = edit(t, s, base, "corrected-searchable")
	var count int64
	s.DB.Raw(`SELECT count(*) FROM transcription_fts WHERE transcription_fts MATCH '"corrected-searchable"'`).Scan(&count)
	if count != 1 {
		t.Fatal("correction not searchable")
	}
	if _, e := s.Save(ctx, "one", Request{ExpectedVersion: base.Version, SegmentTexts: []string{"overwrite"}}); !errors.Is(e, ErrConflict) {
		t.Fatal("lost update", e)
	}
	for i := 0; i < 26; i++ {
		base = current(t, s, "one")
		_ = edit(t, s, base, "a")
		base = current(t, s, "one")
		_ = edit(t, s, base, "b")
	}
	first, e := s.History(ctx, "one", 0)
	if e != nil || len(first) != 50 {
		t.Fatal(len(first), e)
	}
	second, e := s.History(ctx, "one", first[49].ID)
	if e != nil || len(second) != 4 || second[0].ID >= first[49].ID || second[3].Kind != "original" {
		t.Fatal(second, e)
	}
}

func TestConcurrentEditorsCannotBothSave(t *testing.T) {
	s, _ := setup(t)
	base := current(t, s, "one")
	start := make(chan struct{})
	results := make(chan error, 2)
	for _, text := range []string{"first correction", "second correction"} {
		go func(text string) {
			<-start
			_, err := s.Save(context.Background(), "one", Request{ExpectedVersion: base.Version, SegmentTexts: []string{text}})
			results <- err
		}(text)
	}
	close(start)
	success := 0
	for i := 0; i < 2; i++ {
		if <-results == nil {
			success++
		}
	}
	if success != 1 {
		t.Fatalf("expected exactly one committed correction, got %d", success)
	}
	rows, err := s.History(context.Background(), "one", 0)
	if err != nil || len(rows) != 2 {
		t.Fatal("partial or duplicate history", rows, err)
	}
}
