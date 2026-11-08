package api

import (
	"context"
	"strings"
	"testing"

	"quill/internal/llm"
)

type summaryPipelineService struct {
	calls [][]llm.ChatMessage
}

func (s *summaryPipelineService) GetModels(context.Context) ([]string, error) {
	return []string{"test-model"}, nil
}

func (s *summaryPipelineService) GetContextWindow(context.Context, string) (int, error) {
	return 4096, nil
}

func (s *summaryPipelineService) ChatCompletion(_ context.Context, _ string, messages []llm.ChatMessage, _ float64) (*llm.ChatResponse, error) {
	s.calls = append(s.calls, messages)
	response := &llm.ChatResponse{}
	response.Choices = append(response.Choices, struct {
		Index   int `json:"index"`
		Message struct {
			Role    string `json:"role"`
			Content string `json:"content"`
		} `json:"message"`
		FinishReason string `json:"finish_reason"`
	}{})
	response.Choices[0].Message.Content = "Leona raised a concrete concern. River responded with a different view."
	return response, nil
}

func (s *summaryPipelineService) ChatCompletionStream(context.Context, string, []llm.ChatMessage, float64) (<-chan string, <-chan error) {
	return nil, nil
}

func TestPrepareSummaryMessagesUsesDirectPromptForShortTranscript(t *testing.T) {
	svc := &summaryPipelineService{}
	messages, err := prepareSummaryMessages(context.Background(), svc, "test-model", "[Leona] Hello\n[River] Hi", "Write detailed notes.", true)
	if err != nil {
		t.Fatalf("prepare summary messages: %v", err)
	}
	if len(svc.calls) != 0 {
		t.Fatalf("short transcript unexpectedly triggered %d extraction calls", len(svc.calls))
	}
	if len(messages) != 2 || !strings.Contains(messages[0].Content, "exact names") {
		t.Fatalf("speaker guidance missing from direct prompt: %#v", messages)
	}
}

func TestPrepareSummaryMessagesSectionsLongTranscriptAndPreservesSpeakerGuidance(t *testing.T) {
	svc := &summaryPipelineService{}
	line := "[Leona] A concrete account of her position and why it matters.\n[River] A distinct response with disagreement and context.\n"
	transcript := strings.Repeat(line, 180)

	messages, err := prepareSummaryMessages(context.Background(), svc, "test-model", transcript, "Produce deep notes.", true)
	if err != nil {
		t.Fatalf("prepare summary messages: %v", err)
	}
	if len(svc.calls) < 2 {
		t.Fatalf("long transcript should trigger multiple extraction calls, got %d", len(svc.calls))
	}
	for i, call := range svc.calls {
		joined := call[0].Content + call[1].Content
		if !strings.Contains(joined, "speaker") {
			t.Fatalf("call %d omitted speaker guidance", i)
		}
	}
	if len(messages) != 2 || !strings.Contains(messages[0].Content, "Never collapse named people") {
		t.Fatalf("final synthesis prompt omitted named-speaker rule: %#v", messages)
	}
	if !strings.Contains(messages[1].Content, "Leona raised") || !strings.Contains(messages[1].Content, "River responded") {
		t.Fatalf("final synthesis prompt omitted extracted evidence: %s", messages[1].Content)
	}
}

func TestSplitSummaryTextRespectsLineBoundaries(t *testing.T) {
	chunks := splitSummaryText("[Leona] one\n[River] two\n[Leona] three", 25)
	if len(chunks) != 2 {
		t.Fatalf("got %d chunks, want 2: %#v", len(chunks), chunks)
	}
	if chunks[0] != "[Leona] one\n[River] two" {
		t.Fatalf("unexpected first chunk: %q", chunks[0])
	}
}
