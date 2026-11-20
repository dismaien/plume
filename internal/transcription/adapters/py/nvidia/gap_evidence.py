"""Identify unassigned machine words using local acoustic and decoder evidence."""
from bisect import bisect_left, bisect_right
import json
import math

import numpy as np


def clipped_alignment(word):
    score = word.get('score')
    return score is not None and 0 < word['end']-word['start'] <= .35 and 0 <= score < .18


def decoder_windows(metadata, words):
    """Match exact decoder tokens on the compact clock before restoring silence."""
    try:
        diagnostics = json.loads(metadata.get('asr_diagnostics') or '{}')
        regions = json.loads(metadata.get('vad_retained_speech_regions') or '[]')
        if diagnostics.get('decoder') != 'faster_whisper_full_audio' or not regions:
            return {}
        starts = [r['start'] for r in regions]
        ends = [r['end'] for r in regions]
        if any(not math.isfinite(a+b) or a < 0 or b <= a for a, b in zip(starts, ends)):
            return {}
        if any(a < b for a, b in zip(starts[1:], ends)):
            return {}
        compact_ends = np.cumsum(np.subtract(ends, starts))
        if not math.isclose(diagnostics['input_duration_seconds'], compact_ends[-1], abs_tol=.05):
            return {}
        tokens = [w for seg in diagnostics['segments'] for w in seg.get('words') or []]
        for token in tokens:
            if not isinstance(token['word'], str) or not math.isfinite(token['start'] + token['end'] + token.get('probability', 0)):
                return {}
        ordered = sorted((w['start'], i) for i, w in enumerate(tokens))
        ordered_ends = sorted((w['end'], i) for i, w in enumerate(tokens))
    except (ValueError, TypeError, KeyError, AttributeError):
        # Decoder diagnostics are optional in older snapshots.
        return {}
    times = [a for a, _ in ordered]
    finish_times = [b for b, _ in ordered_ends]
    windows = {}
    for i, w in enumerate(words):
        if w.get('speaker_review') or (w.get('speaker') and not clipped_alignment(w)):
            continue
        pos = bisect_right(starts, w['start']) - 1
        if pos < 0:
            continue
        t = compact_ends[pos] - max(0, regions[pos]['end'] - w['start'])
        candidates = {j for _, j in ordered[bisect_left(times, t-.8):bisect_right(times, t+.8)]}
        # A forced alignment can stretch a word backwards across a restored
        # silence. Match the independently timed end as well as the start.
        last = bisect_right(starts, w['end']) - 1
        finish = compact_ends[last] - max(0, ends[last]-w['end'])
        candidates.update(j for _, j in ordered_ends[bisect_left(finish_times, finish-.20):bisect_right(finish_times, finish+.20)])
        matches = []
        for j in candidates:
            d = tokens[j]
            if d['word'].strip() != w['word'].strip() or d.get('probability', 0) < (.90 if w.get('speaker') else .65):
                continue
            if not any(0 <= i+k < len(words) and 0 <= j+k < len(tokens)
                       and tokens[j+k]['word'].strip() == words[i+k]['word'].strip() for k in (-1, 1)):
                continue
            a, b = d['start'], d['end']
            r = bisect_right(compact_ends, a)
            if r >= len(regions) or not 0 < b-a <= 1.6 or b > compact_ends[r] + 1e-6:
                continue
            offset = regions[r]['end'] - compact_ends[r]
            a, b = a + offset, b + offset
            if b >= w['start'] - .16 and a <= w['end'] + .16:
                matches.append((a, b))
        if len(matches) == 1:
            windows[i] = matches[0]
    return windows


def local_voice_evidence(primary, probabilities, mask, step):
    people = sorted({r['speaker'] for r in primary.get('regular_segments', [])})
    if not people or not len(mask):
        return None
    frames = (np.arange(len(mask)) + .5) * step
    # A negative value denotes silence (-1) or more than one voice (-2).
    exclusive = np.full(len(mask), -1, dtype=np.int32)
    for turn in primary.get('segments', []):
        if turn['speaker'] in people:
            a, b = np.searchsorted(frames, [turn['start'], turn['end']])
            exclusive[a:b] = people.index(turn['speaker'])
    primary_voice = np.full(len(mask), -1, dtype=int)
    for turn in primary['regular_segments']:
        a, b = np.searchsorted(frames, [turn['start'], turn['end']])
        track = primary_voice[a:b]
        person = people.index(turn['speaker'])
        track[:] = np.where((track == -1) | (track == person), person, -2)
    active = (probabilities >= .4) & mask[:, None]
    solo = np.where(active.sum(axis=1) == 1, probabilities.argmax(axis=1), -1)
    # Within one uninterrupted native voice, establish identity only from
    # clean primary anchors nearby. A channel can change owner over hours.
    local = np.full(len(mask), -1, dtype=int)
    cuts = np.r_[0, np.flatnonzero(np.diff(solo)) + 1, len(mask)]
    for a, b in zip(cuts, cuts[1:]):
        if solo[a] < 0:
            continue
        observed = primary_voice[a:b]
        anchors = np.flatnonzero(observed >= 0)
        if not len(anchors):
            continue
        voters = observed[anchors]
        if np.all(voters == voters[0]) and len(anchors)*step >= .48:
            idx = np.arange(b-a)
            # Bound extrapolation even across uninterrupted activity.
            ins = np.searchsorted(anchors, idx)
            dist = np.minimum(abs(idx-anchors[np.maximum(ins-1, 0)]),
                              abs(idx-anchors[np.minimum(ins, len(anchors)-1)]))
            local[a:b] = np.where(dist*step <= 3., voters[0], -1)
    # Use a bounded neighbourhood for a short disconnected reply. Channel
    # swaps in a long file make a whole-file one-to-one map unreliable.
    # Count only primary single-speaker time on an independent solo channel.
    bin_size = max(1, round(1/step))
    bins = np.arange(0, len(mask), bin_size)
    halo = max(1, round(45/(bin_size*step)))
    ids = np.arange(len(bins))
    for channel in range(probabilities.shape[1]):
        anchors = (solo == channel) & (primary_voice >= 0)
        if not anchors.any():
            continue
        votes = []
        for person in range(len(people)):
            seconds = np.add.reduceat((anchors & (primary_voice == person)).astype(np.int32), bins) * step
            prefix = np.r_[0., np.cumsum(seconds)]
            votes.append(prefix[np.minimum(ids+halo+1, len(bins))] - prefix[np.maximum(0, ids-halo)])
        votes = np.asarray(votes)
        winner = votes.argmax(axis=0)
        best = votes[winner, ids]
        trusted = (best >= 4.) & (best >= .95 * votes.sum(axis=0))
        unassigned = np.flatnonzero((solo == channel) & (local < 0))
        eligible = trusted[unassigned//bin_size]
        ix = unassigned[eligible]
        local[ix] = winner[ix//bin_size]
    return people, frames, exclusive, primary_voice, active, solo, local


def clipped_word_proposal(index, words, window, evidence, probabilities):
    """An aligner can place the last syllable of a word on a background voice."""
    if not window or index == 0:
        return None
    word = words[index]
    # An isolated acknowledgement can be a complete utterance during overlap.
    if word['word'].casefold().strip(" .,!?…") in {
        'yeah', 'yes', 'no', 'okay', 'ok', 'right', 'mhm', 'mm', 'uh-huh',
        'yep', 'yup', 'nope', 'ja', 'nein', 'genau', 'si', 'sí', 'sim', 'não', 'oui', 'non',
    }:
        return None
    start, end = word['start'], word['end']
    a, b = window
    if not clipped_alignment(word) or start-a < .24 or abs(end-b) > .12 or b-a < 2*(end-start):
        return None
    people, frames, exclusive, voice, _, solo, local = evidence
    left, right = np.searchsorted(frames, [a, b])
    before, after = np.searchsorted(frames, [start-.04, end+.24])
    target = words[index-1].get('speaker')
    if target not in people or target == word['speaker'] or words[index-1]['end'] < a-.4:
        return None
    person = people.index(target)
    # The same voice must occupy the preceding syllables and resume after
    # the intrusion. Also require simultaneous primary speech on that edge.
    inner, outer = np.searchsorted(frames, [start, end])
    if not (left < before <= inner < outer < after) or not np.any(voice[inner:outer] == -2):
        return None
    if np.mean(exclusive[left:before] == person) < .85 or np.mean(exclusive[outer:after] == person) < .75:
        return None
    active = (local[left:right] == person) & (solo[left:right] >= 0)
    if active.sum() < .40*(right-left):
        return None
    core = probabilities[left:right][active]
    channels = solo[left:right][active]
    if not np.all(channels == channels[0]):
        return None
    row = core.mean(axis=0)
    activity = float(row[channels[0]])
    margin = activity - (float(np.partition(row, -2)[-2]) if len(row) > 1 else 0)
    if activity < .9 or margin < .25:
        return None
    return dict(index=index, word=word['word'], start=start, end=end, previous=word['speaker'],
                proposed=target, activity=activity, margin=margin)


def gap_proposals(evidence, probabilities, step, words, metadata):
    if evidence is None:
        return []
    people, frames, exclusive, primary_voice, active, solo, local = evidence
    windows = decoder_windows(metadata, words)
    result = []
    for index, word in enumerate(words):
        if word.get('speaker_review'):
            continue
        if word.get('speaker'):
            proposal = clipped_word_proposal(index, words, windows.get(index), evidence, probabilities)
            if proposal:
                result.append(proposal)
            continue
        start, end = word['start'], word['end']
        if not math.isfinite(start+end) or end <= start:
            continue
        windows_for_word = [(start, end)]
        if index in windows:
            windows_for_word.append(windows[index])
        for a, b in windows_for_word:
            left = max(0, math.floor(a/step))
            right = min(len(frames), math.ceil(b/step))
            if left >= right:
                continue
            # Restrict to frame centres when possible to avoid counting the
            # preceding or following speaker through a sliver of a shared frame.
            inner_a, inner_b = np.searchsorted(frames, [a, b])
            if inner_b > inner_a:
                left, right = inner_a, inner_b
            voice = primary_voice[left:right]
            channels = active[left:right]
            heard = channels.any(axis=1)
            if not heard.any():
                continue
            counts = channels.sum(axis=0)
            channel = int(counts.argmax())
            solo_frames = heard & (solo[left:right] == channel)
            if solo_frames.sum() < .84 * heard.sum():
                continue
            mapped = local[left:right][solo_frames]
            identified = mapped[mapped >= 0]
            if not len(identified) or len(identified) < .85 * len(mapped) or np.any(identified != identified[0]):
                continue
            person = identified[0]
            opposed = (voice == -2) | ((voice >= 0) & (voice != person))
            # Exclusive primary and independently dominant native speech may
            # agree during background overlap. Both must cover the spoken core.
            direct = (voice == person) | ((voice == -2) & (exclusive[left:right] == person))
            if opposed.any() and direct[solo_frames].mean() < .8:
                continue
            # Average the voiced core when the aligner also includes low
            # probability onset/offset frames. A lone peak is insufficient.
            voiced = probabilities[left:right][solo_frames]
            core = voiced[:, channel] >= .9
            nearby_word = any(neighbor.get('speaker') == people[person]
                              and neighbor['start'] < end + .5 and neighbor['end'] > start - .5
                              for neighbor in words[max(0, index-1):index] + words[index+1:index+2])
            if (direct[solo_frames].any() or nearby_word) and core.sum() >= .40*len(voiced) and core.sum()*step >= min(.08, .65*(b-a)):
                voiced = voiced[core]
            row = voiced.mean(axis=0)
            best = row[channel]
            runner = np.partition(row, -2)[-2] if len(row) > 1 else 0
            corroborated = np.count_nonzero(solo_frames & (voice == person))*step
            primary_fraction = np.count_nonzero(solo_frames & (voice == person)) / heard.sum()
            concordant = (primary_fraction >= .8 and corroborated >= min(.08, .7*(b-a))
                          and not opposed.any() and best >= .65 and best-runner >= .4)
            if (best < .9 or best-runner < .25) and not concordant:
                continue
            if b-a <= .20 and corroborated < min(.065, .6*(b-a)):
                # Tiny aligner-only fragments at a handover can belong to
                # either talker. Two clocks must independently locate this
                # token in one uninterrupted, identified native voice.
                if index not in windows:
                    continue
                ds, de = windows[index]
                if min(end, de) <= max(start, ds) or max(end, de)-min(start, ds) > .65:
                    continue
                lo, hi = np.searchsorted(frames, [min(start, ds), max(end, de)])
                if hi <= lo or not np.all(local[lo:hi] == person) or not np.all(solo[lo:hi] == channel):
                    continue
            if (heard.mean() < .22 and corroborated < .06) or (b-a > 2. and heard.sum()*step > 2.):
                continue
            result.append(dict(index=index, word=word['word'], start=start, end=end, previous='',
                               proposed=people[person], gap_evidence=True, activity=float(best),
                               margin=float(best-runner), primary_fraction=float(primary_fraction)))
            break
    return result


def unmapped_spans(evidence, probabilities, mapping, step):
    """Only the solo, locally recognized portion of a globally unmatched channel."""
    if evidence is None:
        return {}
    people, _, _, _, _, solo, local = evidence
    spans = {}
    for channel in range(probabilities.shape[1]):
        key = f'speaker_{channel}'
        if key in mapping:
            continue
        voice = np.where((solo == channel) & (probabilities[:, channel] >= .9), local, -1)
        changes = np.r_[0, np.flatnonzero(np.diff(voice)) + 1, len(voice)]
        rows = [(a*step, b*step, people[voice[a]]) for a, b in zip(changes, changes[1:]) if voice[a] >= 0]
        if rows:
            spans[key] = ([span[1] for span in rows], rows)
    return spans
