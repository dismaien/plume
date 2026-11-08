package api

import (
	"context"
	"fmt"
	"strings"

	"quill/internal/llm"
)

const (
	summaryDirectCharLimit = 12_000
	summaryChunkCharLimit  = 10_000
	summaryReduceCharLimit = 11_000
)

func directSummaryMessages(transcript, instructions string, includeSpeakerInfo bool) []llm.ChatMessage {
	speakerGuidance := "The transcript may not identify speakers. Do not invent identities."
	if includeSpeakerInfo {
		speakerGuidance = "Speaker labels are authoritative. Use the exact names in brackets and never replace an available name with generic phrases such as 'the speaker' or 'they'."
	}

	return []llm.ChatMessage{
		{
			Role: "system",
			Content: "You produce faithful, specific transcript analysis. " + speakerGuidance +
				" Distinguish explicit statements from cautious inference, preserve disagreement and emotional nuance, and never invent decisions, tasks, owners, or facts.",
		},
		{
			Role: "user",
			Content: "Follow these output instructions:\n\n" + strings.TrimSpace(instructions) +
				"\n\n--- TRANSCRIPT ---\n" + strings.TrimSpace(transcript) + "\n--- END TRANSCRIPT ---",
		},
	}
}

// prepareSummaryMessages reduces long transcripts into speaker-attributed evidence
// notes before the final generation. This keeps every part of a long recording in
// scope even when the selected local model has a small runtime context window.
func prepareSummaryMessages(
	ctx context.Context,
	svc llm.Service,
	model string,
	transcript string,
	instructions string,
	includeSpeakerInfo bool,
) ([]llm.ChatMessage, error) {
	transcript = strings.TrimSpace(transcript)
	instructions = strings.TrimSpace(instructions)
	if len(transcript)+len(instructions) <= summaryDirectCharLimit {
		return directSummaryMessages(transcript, instructions, includeSpeakerInfo), nil
	}

	chunks := splitSummaryText(transcript, summaryChunkCharLimit)
	evidence := make([]string, 0, len(chunks))
	for i, chunk := range chunks {
		messages := evidenceExtractionMessages(chunk, instructions, includeSpeakerInfo, i+1, len(chunks))
		text, err := runSummaryCompletion(ctx, svc, model, messages)
		if err != nil {
			return nil, fmt.Errorf("summarize transcript section %d of %d: %w", i+1, len(chunks), err)
		}
		evidence = append(evidence, fmt.Sprintf("## Section %d of %d\n%s", i+1, len(chunks), limitSummaryText(text, 2_500)))
	}

	for summaryTextLength(evidence) > summaryReduceCharLimit && len(evidence) > 1 {
		groups := groupSummaryBlocks(evidence, summaryChunkCharLimit)
		reduced := make([]string, 0, len(groups))
		for i, group := range groups {
			messages := evidenceReductionMessages(group, includeSpeakerInfo, i+1, len(groups))
			text, err := runSummaryCompletion(ctx, svc, model, messages)
			if err != nil {
				return nil, fmt.Errorf("consolidate evidence group %d of %d: %w", i+1, len(groups), err)
			}
			reduced = append(reduced, fmt.Sprintf("## Evidence group %d of %d\n%s", i+1, len(groups), limitSummaryText(text, 3_000)))
		}
		if len(reduced) >= len(evidence) {
			break
		}
		evidence = reduced
	}

	speakerGuidance := "Do not invent speaker identities."
	if includeSpeakerInfo {
		speakerGuidance = "Retain the exact named speaker attribution from the evidence. Never collapse named people into 'the speaker' or generic pronouns when a name is known."
	}

	return []llm.ChatMessage{
		{
			Role: "system",
			Content: "You are writing the final analysis of a long transcript from section-by-section evidence. " + speakerGuidance +
				" Synthesize across the full conversation, preserve contradictions and changes over time, and never invent decisions, action items, owners, or facts.",
		},
		{
			Role: "user",
			Content: "Follow these output instructions:\n\n" + instructions +
				"\n\n--- EVIDENCE FROM THE COMPLETE TRANSCRIPT ---\n" + strings.Join(evidence, "\n\n") +
				"\n--- END EVIDENCE ---",
		},
	}, nil
}

func evidenceExtractionMessages(chunk, instructions string, includeSpeakerInfo bool, index, total int) []llm.ChatMessage {
	speakerGuidance := "Preserve any speaker labels that appear."
	if includeSpeakerInfo {
		speakerGuidance = "Every line begins with a speaker label. Attribute every position, feeling, decision, request, and commitment to that exact named speaker."
	}

	return []llm.ChatMessage{
		{
			Role: "system",
			Content: "Extract a dense evidence ledger from one section of a longer transcript. Do not write the final summary. " + speakerGuidance +
				" Preserve concrete examples, motivations, tensions, emotional shifts, disagreements, agreements, explicit decisions, explicit commitments, and unresolved questions. " +
				"Do not invent action items or infer an owner. Avoid vague phrases such as 'various topics were discussed'. Keep the ledger under 350 words.",
		},
		{
			Role:    "user",
			Content: fmt.Sprintf("Section %d of %d. The eventual output will follow these instructions; use them only to decide what evidence matters:\n\n%s\n\n--- TRANSCRIPT SECTION ---\n%s\n--- END SECTION ---", index, total, instructions, chunk),
		},
	}
}

func evidenceReductionMessages(group string, includeSpeakerInfo bool, index, total int) []llm.ChatMessage {
	speakerGuidance := "Preserve all available speaker attribution."
	if includeSpeakerInfo {
		speakerGuidance = "Keep exact speaker names attached to their views, feelings, requests, decisions, and commitments."
	}
	return []llm.ChatMessage{
		{
			Role: "system",
			Content: "Consolidate evidence notes from a long transcript without flattening them. " + speakerGuidance +
				" Retain concrete examples, disagreement, emotional movement, decisions, commitments, and unresolved questions. " +
				"Remove repetition only. Do not invent anything. Keep the result under 450 words.",
		},
		{
			Role:    "user",
			Content: fmt.Sprintf("Evidence group %d of %d:\n\n%s", index, total, group),
		},
	}
}

func runSummaryCompletion(ctx context.Context, svc llm.Service, model string, messages []llm.ChatMessage) (string, error) {
	resp, err := svc.ChatCompletion(ctx, model, messages, 0.0)
	if err != nil {
		return "", err
	}
	if resp == nil || len(resp.Choices) == 0 {
		return "", fmt.Errorf("model returned no choices")
	}
	text := strings.TrimSpace(resp.Choices[0].Message.Content)
	if text == "" {
		return "", fmt.Errorf("model returned an empty response")
	}
	return text, nil
}

func splitSummaryText(text string, limit int) []string {
	if len(text) <= limit {
		return []string{text}
	}

	lines := strings.Split(text, "\n")
	chunks := make([]string, 0, len(text)/limit+1)
	var current strings.Builder
	flush := func() {
		if current.Len() == 0 {
			return
		}
		chunks = append(chunks, strings.TrimSpace(current.String()))
		current.Reset()
	}

	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if len(line) > limit {
			flush()
			for _, part := range splitLongSummaryLine(line, limit) {
				chunks = append(chunks, part)
			}
			continue
		}
		if current.Len() > 0 && current.Len()+1+len(line) > limit {
			flush()
		}
		if current.Len() > 0 {
			current.WriteByte('\n')
		}
		current.WriteString(line)
	}
	flush()
	return chunks
}

func splitLongSummaryLine(line string, limit int) []string {
	words := strings.Fields(line)
	parts := make([]string, 0, len(line)/limit+1)
	var current strings.Builder
	for _, word := range words {
		if current.Len() > 0 && current.Len()+1+len(word) > limit {
			parts = append(parts, current.String())
			current.Reset()
		}
		if current.Len() > 0 {
			current.WriteByte(' ')
		}
		current.WriteString(word)
	}
	if current.Len() > 0 {
		parts = append(parts, current.String())
	}
	return parts
}

func groupSummaryBlocks(blocks []string, limit int) []string {
	groups := make([]string, 0, len(blocks))
	var current strings.Builder
	for _, block := range blocks {
		if current.Len() > 0 && current.Len()+2+len(block) > limit {
			groups = append(groups, current.String())
			current.Reset()
		}
		if current.Len() > 0 {
			current.WriteString("\n\n")
		}
		current.WriteString(block)
	}
	if current.Len() > 0 {
		groups = append(groups, current.String())
	}
	return groups
}

func summaryTextLength(blocks []string) int {
	total := 0
	for _, block := range blocks {
		total += len(block)
	}
	return total
}

func limitSummaryText(text string, limit int) string {
	text = strings.TrimSpace(text)
	if len(text) <= limit {
		return text
	}
	cut := strings.LastIndexAny(text[:limit], " \n\t")
	if cut < limit/2 {
		cut = limit
	}
	return strings.TrimSpace(text[:cut]) + "…"
}
