import type { Transcript } from '@/features/transcription/hooks/useAudioDetail';

export interface ReviewPassage {
    segment: number;
    first_word: number | null;
    last_word: number | null;
    start: number;
    end: number;
    original: string;
    speaker: string;
    cue: boolean;
}

// Keep separate turns at the same displayed second distinct by their source indices.
export function getReviewPassages(t: Transcript): ReviewPassage[] {
    const segments = t.segments ?? [];
    const words = t.word_segments ?? [];
    const items = words.length ? words : segments;
    const cue = Number(t.metadata?.speaker_review_start ?? NaN);
    const result: ReviewPassage[] = [];
    let nearest: ReviewPassage | undefined;
    let nearestDistance = Infinity;
    let seg = 0;
    for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (!Number.isFinite(item.start) || !Number.isFinite(item.end)) continue;
        const midpoint = (item.start + item.end) / 2;
        while (
            seg + 1 < segments.length &&
            midpoint >= segments[seg + 1].start &&
            midpoint >= segments[seg].end
        )
            seg++;
        const part: ReviewPassage = {
            segment: words.length ? (segments.length ? seg : -1) : i,
            first_word: words.length ? i : null,
            last_word: words.length ? i : null,
            start: item.start,
            end: item.end,
            speaker: item.speaker || '',
            original: ('word' in item ? item.word : item.text).trim(),
            cue: false,
        };
        if (!part.original) continue;
        const distance = Math.max(0, item.start - cue, cue - item.end);
        if (distance < nearestDistance) {
            nearestDistance = distance;
            nearest = part;
        }
        if (item.speaker_reviewed || (item.speaker && !item.speaker_review)) continue;
        const prev = result[result.length - 1];
        if (
            words.length &&
            prev?.segment === part.segment &&
            prev.last_word === i - 1 &&
            prev.speaker === part.speaker &&
            part.start <= prev.end + 0.75
        ) {
            prev.original += ' ' + part.original;
            prev.last_word = i;
            prev.end = Math.max(prev.end, part.end);
        } else result.push(part);
    }
    if (nearest && Number.isFinite(cue) && cue >= 0) {
        const cueItem = nearest;
        const existing = result.find(
            (p) =>
                p.segment === cueItem.segment &&
                (p.first_word === null ||
                    (p.first_word <= cueItem.first_word! && p.last_word! >= cueItem.first_word!)),
        );
        if (existing) existing.cue = true;
        else if (!(words.length ? words[cueItem.first_word!] : segments[cueItem.segment])?.speaker_reviewed)
            result.push({ ...cueItem, cue: true });
    }
    return result.sort((a, b) => a.start - b.start);
}

export function samePassage(a: ReviewPassage, b: ReviewPassage) {
    return (
        a.segment === b.segment &&
        a.first_word === b.first_word &&
        a.last_word === b.last_word &&
        a.start === b.start &&
        a.end === b.end &&
        a.original === b.original &&
        a.speaker === b.speaker
    );
}
