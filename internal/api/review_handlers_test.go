package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
)

func TestReviewMalformedJSONDoesNotReadArchiveOrInvokeModel(t *testing.T) {
	gin.SetMode(gin.TestMode)
	handler := &Handler{} // Nil dependencies would panic if validation read the archive.
	for _, route := range []struct {
		path   string
		handle gin.HandlerFunc
	}{
		{"/review/generate", handler.GenerateConversationReview},
		{"/review/ask", handler.AskConversationArchive},
	} {
		router := gin.New()
		router.POST(route.path, route.handle)
		request := httptest.NewRequest(http.MethodPost, route.path, strings.NewReader("{"))
		request.Header.Set("Content-Type", "application/json")
		response := httptest.NewRecorder()
		router.ServeHTTP(response, request)
		if response.Code != http.StatusBadRequest {
			t.Fatalf("%s returned %d", route.path, response.Code)
		}
	}
}

func TestParseReviewWindowDefaultsAndCapsLimit(t *testing.T) {
	now := time.Date(2026, 11, 7, 12, 0, 0, 0, time.UTC)
	since, until, limit, err := parseReviewWindow(reviewRequest{MaxRecordings: 100}, now)
	if err != nil {
		t.Fatalf("parseReviewWindow: %v", err)
	}
	if !until.Equal(now) || !since.Equal(now.AddDate(0, 0, -7)) {
		t.Fatalf("unexpected range: %s to %s", since, until)
	}
	if limit != reviewMaximumLimit {
		t.Fatalf("limit = %d, want %d", limit, reviewMaximumLimit)
	}
}

func TestExtractReviewExcerptsUsesConfirmedMapping(t *testing.T) {
	payload := reviewTranscriptPayload{}
	for i := 0; i < 6; i++ {
		payload.Segments = append(payload.Segments, struct {
			Start       float64 `json:"start"`
			Text        string  `json:"text"`
			Speaker     string  `json:"speaker"`
			SpeakerName string  `json:"speaker_name"`
		}{Start: float64(i * 10), Text: "useful evidence", Speaker: "speaker_0", SpeakerName: "Speaker A"})
	}
	rawBytes, _ := json.Marshal(payload)
	raw := string(rawBytes)
	excerpts := extractReviewExcerpts(&raw, map[string]string{"speaker_0": "Leona"})
	if len(excerpts) != 4 {
		t.Fatalf("len(excerpts) = %d, want 4", len(excerpts))
	}
	for _, excerpt := range excerpts {
		if excerpt.Speaker != "Leona" {
			t.Fatalf("speaker = %q, want Leona", excerpt.Speaker)
		}
	}
}

func TestDecodeModelJSONStripsCodeFence(t *testing.T) {
	var output reviewAskOutput
	err := decodeModelJSON("```json\n{\"answer\":\"Found it\",\"evidence\":[]}\n```", &output)
	if err != nil {
		t.Fatalf("decodeModelJSON: %v", err)
	}
	if output.Answer != "Found it" {
		t.Fatalf("answer = %q", output.Answer)
	}
}

func TestSanitizeReviewInsightsDropsUnknownSources(t *testing.T) {
	seconds := 12.0
	items := []reviewInsight{
		{Text: "Supported", Evidence: []reviewEvidenceRef{{SourceID: "S1", StartSeconds: &seconds}}},
		{Text: "Invented", Evidence: []reviewEvidenceRef{{SourceID: "S9"}}},
	}
	sources := map[string]reviewSource{"S1": {ID: "S1", DurationMS: 5_000}}
	got := sanitizeReviewInsights(items, sources)
	if len(got) != 1 || got[0].Text != "Supported" {
		t.Fatalf("unexpected insights: %#v", got)
	}
	if got[0].Evidence[0].StartSeconds == nil || *got[0].Evidence[0].StartSeconds != 5 {
		t.Fatalf("timestamp was not clamped to source duration")
	}
}
