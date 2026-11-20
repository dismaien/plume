package api

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"

	"quill/internal/llm"
	"quill/internal/models"
	"quill/internal/repository"

	"github.com/gin-gonic/gin"
)

const (
	reviewDefaultLimit   = 18
	reviewMaximumLimit   = 32
	reviewEvidenceBudget = 28_000
)

type reviewRequest struct {
	Since         string `json:"since"`
	Until         string `json:"until"`
	MaxRecordings int    `json:"max_recordings"`
}

type reviewAskRequest struct {
	reviewRequest
	Question string `json:"question"`
}

type reviewSource struct {
	ID         string          `json:"id"`
	JobID      string          `json:"job_id"`
	Title      string          `json:"title"`
	RecordedAt time.Time       `json:"recorded_at"`
	DurationMS int64           `json:"duration_ms"`
	Speakers   []string        `json:"speakers"`
	HasUnclear bool            `json:"has_unclear_speakers"`
	Summary    string          `json:"-"`
	Excerpts   []reviewExcerpt `json:"-"`
}

type reviewExcerpt struct {
	Start   float64
	Speaker string
	Text    string
}

type reviewEvidenceRef struct {
	SourceID     string   `json:"source_id"`
	StartSeconds *float64 `json:"start_seconds,omitempty"`
}

type reviewInsight struct {
	Text     string              `json:"text"`
	Evidence []reviewEvidenceRef `json:"evidence"`
}

type reviewSection struct {
	ID    string          `json:"id"`
	Title string          `json:"title"`
	Items []reviewInsight `json:"items"`
}

type reviewModelOutput struct {
	Themes       []reviewInsight `json:"themes"`
	People       []reviewInsight `json:"people"`
	Decisions    []reviewInsight `json:"decisions"`
	OpenLoops    []reviewInsight `json:"open_loops"`
	Connections  []reviewInsight `json:"connections"`
	Unidentified []reviewInsight `json:"unidentified"`
}

type reviewAskOutput struct {
	Answer   string              `json:"answer"`
	Evidence []reviewEvidenceRef `json:"evidence"`
}

type reviewTranscriptPayload struct {
	Text     string `json:"text"`
	Segments []struct {
		Start       float64 `json:"start"`
		Text        string  `json:"text"`
		Speaker     string  `json:"speaker"`
		SpeakerName string  `json:"speaker_name"`
	} `json:"segments"`
}

// GenerateConversationReview produces an evidence-linked digest without writing
// to transcripts, summaries, titles, or speaker mappings.
func (h *Handler) GenerateConversationReview(c *gin.Context) {
	var req reviewRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid review request"})
		return
	}

	since, until, limit, err := parseReviewWindow(req, time.Now())
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	jobs, total, err := h.listReviewJobs(c.Request.Context(), since, until, limit, nil)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Could not load recordings for review"})
		return
	}
	if len(jobs) == 0 {
		c.JSON(http.StatusOK, gin.H{
			"generated_at": time.Now(), "since": since, "until": until,
			"recording_count": 0, "total_matching": total,
			"sections": []reviewSection{}, "sources": []reviewSource{},
		})
		return
	}

	sources, evidence, err := h.buildReviewEvidence(c.Request.Context(), jobs, limit)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Could not prepare recording evidence"})
		return
	}

	svc, model, err := h.localReviewService(c.Request.Context())
	if err != nil {
		c.JSON(http.StatusConflict, gin.H{"error": err.Error()})
		return
	}

	messages := []llm.ChatMessage{
		{Role: "system", Content: reviewSystemPrompt()},
		{Role: "user", Content: "Review this evidence from the selected period. Return only the requested JSON object.\n\n" + evidence},
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 8*time.Minute)
	defer cancel()
	text, err := runSummaryCompletion(ctx, svc, model, messages)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "Local review generation failed: " + err.Error()})
		return
	}

	var output reviewModelOutput
	if err := decodeModelJSON(text, &output); err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "Local model returned an unreadable review"})
		return
	}
	sourceSet := reviewSourceSet(sources)
	sections := []reviewSection{
		{ID: "themes", Title: "Recurring themes", Items: sanitizeReviewInsights(output.Themes, sourceSet)},
		{ID: "people", Title: "People and conversations", Items: sanitizeReviewInsights(output.People, sourceSet)},
		{ID: "decisions", Title: "Decisions and commitments", Items: sanitizeReviewInsights(output.Decisions, sourceSet)},
		{ID: "open_loops", Title: "Open loops", Items: sanitizeReviewInsights(output.OpenLoops, sourceSet)},
		{ID: "connections", Title: "Connections across conversations", Items: sanitizeReviewInsights(output.Connections, sourceSet)},
		{ID: "unidentified", Title: "Who was this with?", Items: sanitizeReviewInsights(output.Unidentified, sourceSet)},
	}

	c.JSON(http.StatusOK, gin.H{
		"generated_at": time.Now(), "since": since, "until": until,
		"recording_count": len(sources), "total_matching": total,
		"model": model, "sections": sections, "sources": sources,
	})
}

// AskConversationArchive answers a question from a bounded, FTS-retrieved set
// of local recordings. The response contains only validated source references.
func (h *Handler) AskConversationArchive(c *gin.Context) {
	var req reviewAskRequest
	if err := c.ShouldBindJSON(&req); err != nil || strings.TrimSpace(req.Question) == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "A question is required"})
		return
	}
	if len([]rune(req.Question)) > 1_000 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Question is too long"})
		return
	}

	since, until, limit, err := parseReviewWindow(req.reviewRequest, time.Now())
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	if limit > 14 {
		limit = 14
	}

	ids := h.reviewSearchJobIDs(req.Question, limit*2)
	jobs, _, err := h.listReviewJobs(c.Request.Context(), since, until, limit, ids)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Could not search the conversation archive"})
		return
	}
	if len(jobs) == 0 && len(ids) > 0 {
		jobs, _, err = h.listReviewJobs(c.Request.Context(), since, until, limit, nil)
	}
	if err != nil || len(jobs) == 0 {
		c.JSON(http.StatusOK, gin.H{"answer": "I could not find relevant completed recordings in this period.", "evidence": []reviewEvidenceRef{}, "sources": []reviewSource{}})
		return
	}

	sources, evidence, err := h.buildReviewEvidence(c.Request.Context(), jobs, limit)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Could not prepare recording evidence"})
		return
	}
	svc, model, err := h.localReviewService(c.Request.Context())
	if err != nil {
		c.JSON(http.StatusConflict, gin.H{"error": err.Error()})
		return
	}

	prompt := `Answer the user's question only from the supplied recording evidence. Be direct and useful. Distinguish explicit statements from inference. Never invent a person, event, decision, or commitment. If the evidence is insufficient, say so. Return ONLY JSON in this exact shape: {"answer":"...","evidence":[{"source_id":"S1","start_seconds":12.3}]}. Cite the smallest set of sources that supports the answer.`
	ctx, cancel := context.WithTimeout(c.Request.Context(), 8*time.Minute)
	defer cancel()
	text, err := runSummaryCompletion(ctx, svc, model, []llm.ChatMessage{
		{Role: "system", Content: prompt},
		{Role: "user", Content: "Question: " + strings.TrimSpace(req.Question) + "\n\n" + evidence},
	})
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "Local archive question failed: " + err.Error()})
		return
	}
	var output reviewAskOutput
	if err := decodeModelJSON(text, &output); err != nil || strings.TrimSpace(output.Answer) == "" {
		c.JSON(http.StatusBadGateway, gin.H{"error": "Local model returned an unreadable answer"})
		return
	}
	output.Evidence = sanitizeReviewEvidence(output.Evidence, reviewSourceSet(sources))
	c.JSON(http.StatusOK, gin.H{"answer": strings.TrimSpace(output.Answer), "evidence": output.Evidence, "sources": sources, "model": model})
}

func (h *Handler) localReviewService(ctx context.Context) (llm.Service, string, error) {
	svc, provider, err := h.getLLMServiceForProvider(ctx, "ollama")
	if err != nil {
		return nil, "", fmt.Errorf("Review needs a configured local Ollama model")
	}
	if !strings.EqualFold(provider, "ollama") {
		return nil, "", fmt.Errorf("Review is local-only; activate Ollama in LLM settings")
	}
	model, err := h.resolveAutoTitleModel(ctx, svc, "")
	if err != nil {
		return nil, "", fmt.Errorf("Review could not select a local chat model: %w", err)
	}
	return svc, model, nil
}

func parseReviewWindow(req reviewRequest, now time.Time) (time.Time, time.Time, int, error) {
	until := now
	var err error
	if strings.TrimSpace(req.Until) != "" {
		until, err = time.Parse(time.RFC3339, req.Until)
		if err != nil {
			return time.Time{}, time.Time{}, 0, fmt.Errorf("until must be an RFC3339 timestamp")
		}
	}
	since := until.AddDate(0, 0, -7)
	if strings.TrimSpace(req.Since) != "" {
		since, err = time.Parse(time.RFC3339, req.Since)
		if err != nil {
			return time.Time{}, time.Time{}, 0, fmt.Errorf("since must be an RFC3339 timestamp")
		}
	}
	if !since.Before(until) {
		return time.Time{}, time.Time{}, 0, fmt.Errorf("since must be before until")
	}
	limit := req.MaxRecordings
	if limit <= 0 {
		limit = reviewDefaultLimit
	}
	if limit > reviewMaximumLimit {
		limit = reviewMaximumLimit
	}
	return since, until, limit, nil
}

func (h *Handler) listReviewJobs(ctx context.Context, since, until time.Time, limit int, ids []string) ([]models.TranscriptionJob, int64, error) {
	var vaultID *uint
	if activeVault, err := getActiveVault(); err == nil {
		vaultID = &activeVault.ID
	}
	params := repository.ListParams{
		Offset: 0, Limit: limit * 2, SortBy: "recorded_at", SortOrder: "desc",
		Status: string(models.StatusCompleted), RecordedAfter: &since, RecordedBefore: &until,
		VaultID: vaultID, FTSJobIDs: ids,
		Columns: []string{"id", "title", "status", "original_filename", "audio_duration_ms", "recorded_at", "created_at", "summary"},
	}
	return h.jobRepo.ListWithParams(ctx, params)
}

func (h *Handler) buildReviewEvidence(ctx context.Context, jobs []models.TranscriptionJob, limit int) ([]reviewSource, string, error) {
	sources := make([]reviewSource, 0, limit)
	var packet strings.Builder
	for _, job := range jobs {
		if len(sources) >= limit || packet.Len() >= reviewEvidenceBudget {
			break
		}
		title := strings.TrimSpace(job.OriginalFilename)
		if job.Title != nil && strings.TrimSpace(*job.Title) != "" {
			title = strings.TrimSpace(*job.Title)
		}
		if title == "" {
			title = "Untitled recording"
		}

		summary := ""
		if latest, err := h.summaryRepo.GetLatestSummary(ctx, job.ID); err == nil {
			summary = strings.TrimSpace(latest.Content)
		} else if job.Summary != nil {
			summary = strings.TrimSpace(*job.Summary)
		}

		mappings, _ := h.speakerMappingRepo.ListByJob(ctx, job.ID)
		mappingNames := make(map[string]string, len(mappings))
		speakerSet := make(map[string]struct{})
		hasUnclear := false
		for _, mapping := range mappings {
			name := strings.TrimSpace(mapping.CustomName)
			if mapping.IsConfirmedIdentity() && name != "" && !strings.EqualFold(name, mapping.OriginalSpeaker) {
				mappingNames[mapping.OriginalSpeaker] = name
				speakerSet[name] = struct{}{}
			} else {
				hasUnclear = true
			}
		}

		fullJob, err := h.jobRepo.FindByID(ctx, job.ID)
		if err != nil {
			continue
		}
		excerpts := extractReviewExcerpts(fullJob.Transcript, mappingNames)
		for _, excerpt := range excerpts {
			normalizedSpeaker := strings.ToLower(strings.TrimSpace(excerpt.Speaker))
			if strings.HasPrefix(normalizedSpeaker, "speaker_") || strings.HasPrefix(normalizedSpeaker, "speaker ") || normalizedSpeaker == "speaker unclear" {
				hasUnclear = true
			}
		}
		if summary == "" {
			summary = truncateReviewText(extractTranscriptText(fullJob.Transcript), 1_600)
		}
		if summary == "" && len(excerpts) == 0 {
			continue
		}

		speakers := make([]string, 0, len(speakerSet))
		for name := range speakerSet {
			speakers = append(speakers, name)
		}
		sort.Strings(speakers)
		recordedAt := job.CreatedAt
		if job.RecordedAt != nil {
			recordedAt = *job.RecordedAt
		}
		source := reviewSource{
			ID: "S" + strconv.Itoa(len(sources)+1), JobID: job.ID, Title: title,
			RecordedAt: recordedAt, DurationMS: job.AudioDurationMillis,
			Speakers: speakers, HasUnclear: hasUnclear, Summary: truncateReviewText(summary, 1_600), Excerpts: excerpts,
		}
		block := formatReviewSource(source)
		if packet.Len()+len(block) > reviewEvidenceBudget && len(sources) > 0 {
			break
		}
		packet.WriteString(block)
		sources = append(sources, source)
	}
	return sources, packet.String(), nil
}

func extractReviewExcerpts(raw *string, mappings map[string]string) []reviewExcerpt {
	if raw == nil || strings.TrimSpace(*raw) == "" {
		return nil
	}
	var payload reviewTranscriptPayload
	if json.Unmarshal([]byte(*raw), &payload) != nil || len(payload.Segments) == 0 {
		text := truncateReviewText(extractTranscriptText(raw), 320)
		if text == "" {
			return nil
		}
		return []reviewExcerpt{{Start: 0, Text: text}}
	}
	indices := []int{0, len(payload.Segments) / 3, (len(payload.Segments) * 2) / 3, len(payload.Segments) - 1}
	seen := make(map[int]struct{})
	out := make([]reviewExcerpt, 0, 4)
	for _, index := range indices {
		if _, ok := seen[index]; ok || index < 0 || index >= len(payload.Segments) {
			continue
		}
		seen[index] = struct{}{}
		segment := payload.Segments[index]
		text := truncateReviewText(segment.Text, 260)
		if text == "" {
			continue
		}
		speaker := strings.TrimSpace(segment.SpeakerName)
		if mapped := strings.TrimSpace(mappings[segment.Speaker]); mapped != "" {
			speaker = mapped
		} else if speaker == "" {
			speaker = strings.TrimSpace(segment.Speaker)
		}
		out = append(out, reviewExcerpt{Start: math.Max(0, segment.Start), Speaker: speaker, Text: text})
	}
	return out
}

func formatReviewSource(source reviewSource) string {
	var b strings.Builder
	b.WriteString("\n--- SOURCE " + source.ID + " ---\n")
	b.WriteString("Title: " + source.Title + "\n")
	b.WriteString("Recorded: " + source.RecordedAt.Format(time.RFC3339) + "\n")
	if len(source.Speakers) > 0 {
		b.WriteString("Confirmed people: " + strings.Join(source.Speakers, ", ") + "\n")
	} else {
		b.WriteString("Confirmed people: none\n")
	}
	if source.HasUnclear {
		b.WriteString("Speaker identity status: one or more speakers are unresolved\n")
	}
	if source.Summary != "" {
		b.WriteString("Summary: " + source.Summary + "\n")
	}
	for _, excerpt := range source.Excerpts {
		label := formatReviewTimestamp(excerpt.Start)
		if excerpt.Speaker != "" {
			label += " " + excerpt.Speaker
		}
		b.WriteString("Excerpt [" + label + "]: " + excerpt.Text + "\n")
	}
	b.WriteString("--- END SOURCE " + source.ID + " ---\n")
	return b.String()
}

func reviewSystemPrompt() string {
	return `You review a private, local archive of voice memos and conversations. Find useful patterns without flattening nuance. Separate explicit facts from cautious inference. Never invent identities, decisions, commitments, or topics. Use only confirmed people names supplied in evidence. Put conversations with unresolved but potentially valuable speaker identities in unidentified. Return ONLY valid JSON with exactly these keys: {"themes":[{"text":"...","evidence":[{"source_id":"S1","start_seconds":12.3}]}],"people":[],"decisions":[],"open_loops":[],"connections":[],"unidentified":[]}. Keep each item to one or two sentences, provide 0-5 items per section, and cite at least one valid source per item. Use timestamps from supplied excerpts when one supports the claim; otherwise omit start_seconds.`
}

func decodeModelJSON(text string, target any) error {
	trimmed := strings.TrimSpace(text)
	if strings.HasPrefix(trimmed, "```") {
		if newline := strings.IndexByte(trimmed, '\n'); newline >= 0 {
			trimmed = trimmed[newline+1:]
		}
		trimmed = strings.TrimSuffix(strings.TrimSpace(trimmed), "```")
	}
	start := strings.IndexByte(trimmed, '{')
	end := strings.LastIndexByte(trimmed, '}')
	if start < 0 || end < start {
		return fmt.Errorf("JSON object not found")
	}
	return json.Unmarshal([]byte(trimmed[start:end+1]), target)
}

func reviewSourceSet(sources []reviewSource) map[string]reviewSource {
	out := make(map[string]reviewSource, len(sources))
	for _, source := range sources {
		out[source.ID] = source
	}
	return out
}

func sanitizeReviewInsights(items []reviewInsight, sources map[string]reviewSource) []reviewInsight {
	out := make([]reviewInsight, 0, len(items))
	for _, item := range items {
		item.Text = strings.TrimSpace(item.Text)
		item.Evidence = sanitizeReviewEvidence(item.Evidence, sources)
		if item.Text != "" && len(item.Evidence) > 0 {
			out = append(out, item)
		}
		if len(out) == 5 {
			break
		}
	}
	return out
}

func sanitizeReviewEvidence(refs []reviewEvidenceRef, sources map[string]reviewSource) []reviewEvidenceRef {
	out := make([]reviewEvidenceRef, 0, len(refs))
	seen := make(map[string]struct{})
	for _, ref := range refs {
		ref.SourceID = strings.TrimSpace(ref.SourceID)
		source, ok := sources[ref.SourceID]
		if !ok {
			continue
		}
		key := ref.SourceID
		if ref.StartSeconds != nil {
			seconds := math.Max(0, *ref.StartSeconds)
			if source.DurationMS > 0 {
				seconds = math.Min(seconds, float64(source.DurationMS)/1000)
			}
			ref.StartSeconds = &seconds
			key += fmt.Sprintf(":%.1f", seconds)
		}
		if _, exists := seen[key]; exists {
			continue
		}
		seen[key] = struct{}{}
		out = append(out, ref)
	}
	return out
}

func (h *Handler) reviewSearchJobIDs(question string, limit int) []string {
	if h.ftsManager == nil {
		return nil
	}
	queries := []string{question}
	queries = append(queries, reviewSearchTerms(question)...)
	seen := make(map[string]struct{})
	ids := make([]string, 0, limit)
	for _, query := range queries {
		results, err := h.ftsManager.Search(query, limit)
		if err != nil {
			continue
		}
		for _, result := range results {
			if _, ok := seen[result.JobID]; ok {
				continue
			}
			seen[result.JobID] = struct{}{}
			ids = append(ids, result.JobID)
			if len(ids) >= limit {
				return ids
			}
		}
	}
	return ids
}

func reviewSearchTerms(question string) []string {
	stop := map[string]bool{"about": true, "after": true, "before": true, "could": true, "from": true, "have": true, "what": true, "when": true, "where": true, "which": true, "with": true, "would": true, "said": true, "talk": true, "talked": true, "conversation": true, "conversations": true, "recording": true, "recordings": true, "that": true, "this": true, "there": true, "were": true, "your": true, "mine": true}
	clean := strings.Map(func(r rune) rune {
		if unicode.IsLetter(r) || unicode.IsNumber(r) || unicode.IsSpace(r) {
			return unicode.ToLower(r)
		}
		return ' '
	}, question)
	terms := make([]string, 0, 6)
	seen := make(map[string]struct{})
	for _, word := range strings.Fields(clean) {
		if len([]rune(word)) < 3 || stop[word] {
			continue
		}
		if _, ok := seen[word]; ok {
			continue
		}
		seen[word] = struct{}{}
		terms = append(terms, word)
		if len(terms) == 6 {
			break
		}
	}
	return terms
}

func truncateReviewText(text string, limit int) string {
	text = strings.Join(strings.Fields(strings.TrimSpace(text)), " ")
	if len(text) <= limit {
		return text
	}
	cut := strings.LastIndex(text[:limit], " ")
	if cut < limit/2 {
		cut = limit
	}
	return strings.TrimSpace(text[:cut]) + "..."
}

func formatReviewTimestamp(seconds float64) string {
	total := int(math.Max(0, seconds))
	hours := total / 3600
	minutes := (total % 3600) / 60
	secs := total % 60
	if hours > 0 {
		return fmt.Sprintf("%d:%02d:%02d", hours, minutes, secs)
	}
	return fmt.Sprintf("%d:%02d", minutes, secs)
}
