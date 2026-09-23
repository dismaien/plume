import type { Transcript } from "./useAudioDetail";
import { formatSpeakerLabel } from "../../../lib/speaker-utils";

export interface TranscriptDownloadOptions {
    includeTimestamps: boolean;
    includeSpeakerLabels: boolean;
}

export function formatTranscriptTimestamp(seconds: number): string {
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = Math.floor(seconds % 60);
    return `${minutes}:${remainingSeconds.toString().padStart(2, "0")}`;
}

export function getDisplaySpeakerName(originalSpeaker: string, mappings: Record<string, string>): string {
    return mappings[originalSpeaker] || formatSpeakerLabel(originalSpeaker);
}

function escapeMarkdownInline(value: string): string {
    return value.replace(/[\\`*_[\]<>~]/g, "\\$&");
}

export function buildMarkdownTranscript(
    transcript: Transcript,
    filenameBase: string,
    speakerMappings: Record<string, string>,
    options: TranscriptDownloadOptions,
): string {
    const heading = `# ${filenameBase}`;

    if (!options.includeSpeakerLabels && !options.includeTimestamps) {
        return `${heading}\n\n${transcript.text}`.trim();
    }

    if (!transcript.segments?.length) {
        return `${heading}\n\n${transcript.text}`.trim();
    }

    const blocks: Array<{ rawSpeaker: string; speaker: string; start: number; text: string[] }> = [];

    transcript.segments.forEach((segment) => {
        const text = (segment.text ?? "").trim();
        if (!text) return;

        const rawSpeaker = options.includeSpeakerLabels ? segment.speaker ?? "" : "";
        const speaker = rawSpeaker
            ? getDisplaySpeakerName(rawSpeaker, speakerMappings).replace(/\s+/g, " ").trim() || formatSpeakerLabel(rawSpeaker)
            : "";
        const previous = blocks.at(-1);
        // Join on the raw speaker key: distinct speakers sharing a display name stay separate,
        // and unlabelled segments keep their own timestamps.
        const canJoinPrevious = rawSpeaker !== ""
            && previous
            && previous.rawSpeaker === rawSpeaker;

        if (canJoinPrevious) {
            previous.text.push(text);
            return;
        }

        blocks.push({ rawSpeaker, speaker, start: segment.start, text: [text] });
    });

    const body = blocks.map((block) => {
        const labelParts: string[] = [];
        if (block.speaker) {
            labelParts.push(`**${escapeMarkdownInline(block.speaker)}**`);
        }
        if (options.includeTimestamps) {
            labelParts.push(`_[${formatTranscriptTimestamp(block.start)}]_`);
        }

        const text = block.text.join(" ");
        return labelParts.length > 0
            ? `${labelParts.join(" ")}\n\n${text}`
            : text;
    }).join("\n\n");

    return `${heading}\n\n${body}`.trim();
}
