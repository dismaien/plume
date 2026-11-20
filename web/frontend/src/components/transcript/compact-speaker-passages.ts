interface Passage {
    start: number;
    end: number;
    text: string;
    speaker?: string;
    speaker_name?: string;
}

export function compactSpeakerPassages(
    segments: Passage[],
    nameFor: (speaker: string, saved?: string) => string,
    disputedStarts: Set<number>,
) {
    const groups: Array<{
        displaySpeaker: string;
        identityKey: string;
        startTime: number;
        endTime: number;
        segmentIndices: number[];
    }> = [];

    segments.forEach((segment, i) => {
        const speaker = segment.speaker?.trim() || '';
        const displaySpeaker = nameFor(speaker, segment.speaker_name).trim();
        const identityKey = displaySpeaker.toLowerCase();
        const last = groups[groups.length - 1];
        const previous = segments[i - 1];
        const next = segments[i + 1];

        // An explicitly marked word can be read inline between two parts of
        // one speaker's sentence. Keep its original section and seek target.
        const inlineUnknown = !speaker && previous?.speaker && previous.speaker === next?.speaker &&
            !disputedStarts.has(segment.start) && /^\S+$/u.test(segment.text.trim()) &&
            !/[.!?…]["'’”)]*$/u.test(previous.text.trim()) && !/[.!?…]["'’”)]*$/u.test(segment.text.trim()) &&
            !/^(?:yeah|yes|yep|yup|no|nope|ok|okay|right|mhm|mm|mm-hmm|uh-huh|ja|nein|genau|doch|sim|não|sí|oui|non)[,;:]*$/iu.test(segment.text.trim()) &&
            segment.end >= segment.start && segment.end - segment.start <= 3 &&
            segment.start - previous.end <= 0.7 && next.start - segment.end <= 0.7;

        if (last && ((speaker && last.identityKey === identityKey) || inlineUnknown)) {
            last.segmentIndices.push(i);
            last.endTime = Math.max(last.endTime, segment.end);
        } else {
            groups.push({ displaySpeaker, identityKey, startTime: segment.start, endTime: segment.end, segmentIndices: [i] });
        }
    });
    return groups;
}
