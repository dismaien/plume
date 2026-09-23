import { expect, test } from "@playwright/test";
import { buildMarkdownTranscript } from "../src/features/transcription/hooks/transcript-download-format";

test.describe("Markdown transcript formatting", () => {
    test("puts each speaker label before that speaker's dialogue", () => {
        const markdown = buildMarkdownTranscript({
            text: "Leona opens. River replies. River continues. Leona closes.",
            segments: [
                { start: 0, end: 3, text: "Leona opens.", speaker: "speaker_00" },
                { start: 3, end: 6, text: "River replies.", speaker: "speaker_01" },
                { start: 6, end: 9, text: "River continues.", speaker: "speaker_01" },
                { start: 9, end: 12, text: "Leona closes.", speaker: "speaker_00" },
            ],
        }, "Relationship Conflict and Resolution", {
            speaker_00: "Leona",
            speaker_01: "River",
        }, {
            includeSpeakerLabels: true,
            includeTimestamps: true,
        });

        expect(markdown).toBe(`# Relationship Conflict and Resolution

**Leona** _[0:00]_

Leona opens.

**River** _[0:03]_

River replies. River continues.

**Leona** _[0:09]_

Leona closes.`);
        expect(markdown).not.toContain("Leona opens. **River**");
    });

    test("keeps timestamp-only segments in separate blocks", () => {
        const markdown = buildMarkdownTranscript({
            text: "First thought. Second thought.",
            segments: [
                { start: 0, end: 3, text: "First thought." },
                { start: 65, end: 68, text: "Second thought." },
            ],
        }, "Voice Note", {}, {
            includeSpeakerLabels: false,
            includeTimestamps: true,
        });

        expect(markdown).toContain("_[0:00]_\n\nFirst thought.\n\n_[1:05]_\n\nSecond thought.");
    });
});
