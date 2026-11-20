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

    const sentenceEnd = /[.!?…]["'’”)]*$/u;
    const reply = /^(?:yeah|yes|yep|yup|no|nope|ok|okay|right|mhm|mm|mm-hmm|uh-huh|ah|oh|huh|really|sure|thanks|ja|nein|genau|doch|sim|não|sí|oui|non)\b/iu;
    const close = (left: Passage, right: Passage) =>
        right.start >= left.end - 0.12 && right.start <= left.end + 0.7;
    const speakerOf = (row: Passage) => row.speaker?.trim() || row.speaker_name?.trim() || '';
    const owners = segments.map(speakerOf);
    const hasSpeakers = owners.some(Boolean);
    const disputed = [...disputedStarts].sort((a, b) => a-b);
    const needsReview = (row: Passage) => {
        let lo = 0, hi = disputed.length;
        while (lo < hi) {
            const mid = Math.floor((lo + hi) / 2);
            if (disputed[mid] < row.start) lo = mid+1;
            else hi = mid;
        }
        return lo < disputed.length && (disputed[lo] === row.start || disputed[lo] < row.end);
    };
    // Attach a short unfinished fragment to its sentence for reading only.
    // Each such span keeps its own explicit uncertainty label and seek target.
    for (let i = 0; i < segments.length; i++) {
        const row = segments[i];
        if (owners[i] || row.end < row.start || row.end - row.start > 3 || needsReview(row)) continue;
        const text = row.text.trim();
        if (!text || text.split(/\s+/u).length > 4 || reply.test(text) || /^\p{Lu}/u.test(text) && sentenceEnd.test(text)) continue;
        const left = segments[i-1], right = segments[i+1];
        const previous = left && speakerOf(left), next = right && speakerOf(right);
        const joinsLeft = previous && close(left, row) && !sentenceEnd.test(left.text.trim());
        const joinsRight = next && close(row, right) && !sentenceEnd.test(text);
        // When the voices differ, let punctuation determine which side owns
        // the sentence. Leave an unresolved change between them on its own.
        if (joinsLeft && (!joinsRight || previous === next)) owners[i] = previous;
        else if (joinsRight && !joinsLeft) owners[i] = next;
    }

    segments.forEach((segment, i) => {
        const speaker = owners[i];
        const neighbor = !speakerOf(segment) && speaker
            ? [segments[i-1], segments[i+1]].find(row => row && speakerOf(row) === speaker)
            : undefined;
        const displaySpeaker = nameFor(neighbor?.speaker ?? segment.speaker ?? speaker,
            neighbor?.speaker_name ?? segment.speaker_name).trim();
        const identityKey = displaySpeaker.toLowerCase();
        const last = groups[groups.length - 1];
        const continuousUnknown = hasSpeakers && !speaker && i > 0 && !owners[i-1] &&
            close(segments[i-1], segment) && !needsReview(segment) && !needsReview(segments[i-1]) &&
            !sentenceEnd.test(segments[i-1].text.trim()) && !reply.test(segment.text.trim());

        if (last && last.identityKey === identityKey && (speaker || continuousUnknown)) {
            last.segmentIndices.push(i);
            last.endTime = Math.max(last.endTime, segment.end);
        } else {
            groups.push({ displaySpeaker, identityKey, startTime: segment.start, endTime: segment.end, segmentIndices: [i] });
        }
    });
    return groups;
}
