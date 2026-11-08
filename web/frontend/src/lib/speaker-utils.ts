/**
 * Converts a numeric speaker index (0, 1, 2, …) to a human-friendly label
 * ("Speaker A", "Speaker B", "Speaker C", …). Uses base-26 alphabetic conversion
 * so indices beyond 25 produce labels like "Speaker AA", "Speaker AB", etc.
 */
export function speakerIndexToLabel(index: number): string {
    let n = index + 1;
    let label = "";
    while (n > 0) {
        const rem = (n - 1) % 26;
        label = String.fromCharCode(65 + rem) + label;
        n = Math.floor((n - 1) / 26);
    }
    return `Speaker ${label}`;
}

/**
 * Converts a raw speaker key like "speaker_0", "SPEAKER_2", or "Speaker 1"
 * into a human-friendly label like "Speaker A", "Speaker B", etc.
 *
 * Returns the original value unchanged if it doesn't match a known pattern.
 */
export function formatSpeakerLabel(rawSpeaker: string): string {
    // Match patterns like "speaker_0", "SPEAKER_2", "speaker 1"
    const match = rawSpeaker.match(/^speaker[_ ](\d+)$/i);
    if (match) {
        return speakerIndexToLabel(parseInt(match[1], 10));
    }
    return rawSpeaker;
}

/** Collect detected keys for editing; their order must not determine letter labels. */
export function getTranscriptSpeakerOrder(transcript?: {
    segments?: { speaker?: string }[];
    word_segments?: { speaker?: string }[];
} | null): string[] {
    const from = (items?: { speaker?: string }[]) =>
        [...new Set(items?.map(item => item.speaker).filter((speaker): speaker is string => !!speaker))];
    const speakers = from(transcript?.segments);
    return speakers.length ? speakers : from(transcript?.word_segments);
}
