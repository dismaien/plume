"""Check recurring speaker labels against fresh, disjoint samples of their audio."""

from collections import Counter, defaultdict
from itertools import combinations


# Four disjoint sets each test five different turns per recurring label.
# Labels with too few examples (including overlap-only voices) remain independent.
PANELS = 4
TURNS = 5
SECONDS = 3


def reference_panels(segments):
    by_speaker = defaultdict(list)
    for turn in segments:
        if turn['end'] - turn['start'] >= SECONDS:
            by_speaker[turn['speaker']].append((turn['start'] + turn['end']) / 2)
    groups = {s: sorted(times) for s, times in by_speaker.items()
              if len(times) >= PANELS * TURNS}
    # The panel must include a distinct voice as a positive control. Limit the
    # additional inference cost on recordings with many recurring participants.
    if not 3 <= len(groups) <= 8:
        return []
    panels = [[] for _ in range(PANELS)]
    for speaker, times in groups.items():
        for index in range(TURNS * PANELS):
            point = times[(2 * index + 1) * len(times) // (2 * TURNS * PANELS)]
            panels[index % PANELS].append((point, speaker))
    return [sorted(panel) for panel in panels]


def panel_votes(panel, sample, predict):
    import torch

    parts = []
    offset = 0
    ranges = []
    for mid, speaker in panel:
        audio, rate = sample(mid - SECONDS / 2, mid + SECONDS / 2)
        parts.extend((audio, audio.new_zeros((audio.shape[0], rate // 2))))
        ranges.append((offset / rate, (offset + audio.shape[1]) / rate, speaker))
        offset += audio.shape[1] + rate // 2
    result = predict({'sample_rate': rate, 'waveform': torch.cat(parts, dim=1)})
    tracks = list(result.exclusive_speaker_diarization.itertracks(yield_label=True))
    votes = defaultdict(Counter)
    for start, end, speaker in ranges:
        seconds = Counter()
        for turn, _, label in tracks:
            seconds[label] += max(0, min(end, turn.end) - max(start, turn.start))
        if seconds:
            label, duration = seconds.most_common(1)[0]
            if duration >= SECONDS * .6:
                votes[speaker][label] += 1
    return votes


def dominant_labels(votes):
    dominant = {}
    for speaker, counts in votes.items():
        if counts:
            leader, n = counts.most_common(1)[0]
            if n >= TURNS - 2 and n >= .8 * sum(counts.values()):
                dominant[speaker] = leader
    return dominant


def supported_pairs(panels, regular):
    if len(panels) != PANELS:
        return set()
    agree, differ = Counter(), set()
    for votes in panels:
        dominant = dominant_labels(votes)
        if len(set(dominant.values())) < 2:
            continue
        for a, b in combinations(sorted(dominant), 2):
            if dominant[a] == dominant[b]:
                agree[a, b] += 1
            else:
                differ.add((a, b))
    pairs = {pair for pair, n in agree.items() if n >= PANELS - 1 and pair not in differ}
    if not pairs:
        return set()

    # Repeated simultaneous speech is evidence that two labels can speak
    # independently. A few frames at a boundary can arise from segmentation.
    durations = Counter()
    overlap = Counter()
    longest = Counter()
    active = []
    for row in sorted(regular, key=lambda t: t['start']):
        s, e, label = row['start'], row['end'], row['speaker']
        active = [turn for turn in active if turn['end'] > s]
        durations[label] += e - s
        for other in active:
            pair = tuple(sorted((label, other['speaker'])))
            if pair in pairs:
                seconds = min(e, other['end']) - s
                overlap[pair] += seconds
                longest[pair] = max(longest[pair], seconds)
        active.append(row)
    return {p for p in pairs if longest[p] < 2 and
            overlap[p] < .02 * min(durations[s] for s in p)}


def consensus_aliases(panels, regular):
    pairs = supported_pairs(panels, regular)
    # Require direct evidence for every pair within a merged group.
    groups = []
    for speaker in sorted({s for pair in pairs for s in pair}):
        for group in groups:
            if all(tuple(sorted((s, speaker))) in pairs for s in group):
                group.append(speaker)
                break
        else:
            groups.append([speaker])
    return {s: group[0] for group in groups for s in group[1:]}


def check_aliases(segments, regular, sample, predict):
    panels = reference_panels(segments)
    votes = [panel_votes(panel, sample, predict) for panel in panels]
    mapping = consensus_aliases(votes, regular)
    return mapping, votes


def mixed_tracks(panels, aliases, regular):
    """A track following different corroborated voices needs turn-level review."""
    observed = defaultdict(Counter)
    unexplained = set()
    members = set(aliases) | set(aliases.values())
    for panel in panels:
        winners = dominant_labels(panel)
        local = defaultdict(set)
        for speaker in members.intersection(winners):
            local[winners[speaker]].add(aliases.get(speaker, speaker))
        for speaker in winners.keys() - members:
            targets = local[winners[speaker]]
            if not targets and len(set(winners[s] for s in members.intersection(winners))) > 1:
                unexplained.add(speaker)
            if len(targets) == 1:
                observed[speaker][next(iter(targets))] += 1
    mixed = {s: sorted(hits) for s, hits in observed.items()
             if s not in unexplained and len(hits) > 1 and sum(hits.values()) >= PANELS - 1}
    if mixed:
        # A third voice active alongside every proposed identity can be a real
        # separate participant, even if it is only a single brief reply.
        active = []
        for turn in sorted(regular, key=lambda r: r['start']):
            active = [r for r in active if r['end'] > turn['start']]
            active.append(turn)
            speaking = {aliases.get(r['speaker'], r['speaker']) for r in active}
            for speaker in speaking.intersection(mixed):
                if speaking.issuperset(mixed[speaker]):
                    del mixed[speaker]
    return mixed
