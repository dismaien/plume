#!/usr/bin/env python3
"""Verify existing machine word labels with short clips and two local voice panels.

Produce an immutable proposal file for transcription-eval. Supply the original
16 kHz mono waveform, the assembled machine transcript, its native activity
result and channel mapping, and a locally cached TitaNet .nemo model.
"""
import argparse
from bisect import bisect_right
from collections import defaultdict
from functools import lru_cache
import hashlib
from itertools import groupby
import json
import os
from pathlib import Path
import sys
import wave

import numpy as np
from scipy.special import expit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'internal/transcription/adapters/py/nvidia'))
from activity_timeline import file_hash, validate
from gap_evidence import is_reply


def unit(vector):
    return vector / max(np.linalg.norm(vector), 1e-9)


def clip_encoder(reader, model_path):
    # Defer the costly model load until the first eligible comparison.
    @lru_cache(maxsize=1)
    def model():
        import torch
        from nemo.collections.asr.models import EncDecSpeakerLabelModel
        torch.set_num_threads(3)
        return EncDecSpeakerLabelModel.restore_from(str(model_path), map_location='cpu').eval()

    @lru_cache(maxsize=1024)
    def encode(start, end):
        import torch
        a, b = round(start * 16000), round(end * 16000)
        reader.setpos(a)
        samples = np.frombuffer(reader.readframes(b - a), dtype='<i2').astype(np.float32) / 32768
        with torch.no_grad():
            vector, _ = model().infer_segment(samples)
        return unit(vector.cpu().numpy().ravel())

    return encode


def reference_samples(regular, frames, step):
    # Sample non-overlapping utterances. Mark any simultaneous people ineligible.
    people = sorted({turn['speaker'] for turn in regular})
    single = np.full(len(frames), -1, dtype=int)
    for turn in regular:
        a, b = np.searchsorted(frames, [turn['start'], turn['end']])
        current = single[a:b]
        speaker = people.index(turn['speaker'])
        current[:] = np.where((current == -1) | (current == speaker), speaker, -2)

    pools = defaultdict(list)
    cuts = np.r_[0, np.flatnonzero(np.diff(single)) + 1, len(single)]
    for a, b in zip(cuts, cuts[1:]):
        if single[a] < 0:
            continue
        # Alternate disjoint samples between the two comparison panels.
        for x in range(a + round(.05 / step), b - round(1.04 / step), max(1, round(1.90 / step))):
            y = min(b - round(.05 / step), x + round(1.80 / step))
            if (y - x) * step >= 1:
                pools[people[single[a]]].append((x * step, y * step))
    return pools


def compare_clip(start, end, pools, previous, proposed, encode):
    panels = [{}, {}]
    sample = None
    for speaker, intervals in pools.items():
        near = [(a, b) for a, b in intervals if (a >= end + 1 or b <= start - 1)
                and abs((a + b - start - end) / 2) <= 90]
        if len(near) < 2:
            continue
        if sample is None:
            sample = encode(start, end)
        for fold, scores in enumerate(panels):
            refs = near[fold::2]
            indices = np.unique(np.linspace(0, len(refs) - 1, min(4, len(refs)), dtype=int))
            centroid = unit(np.mean([encode(*refs[i]) for i in indices], axis=0))
            scores[speaker] = float(sample @ centroid)

    winners = []
    for scores in panels:
        if previous not in scores or proposed not in scores:
            return None
        first, second = sorted(scores, key=scores.get, reverse=True)[:2]
        if scores[first] < .30 or scores[first] - scores[second] < .25:
            return None
        winners.append(first)
    return winners[0] if winners[0] == winners[1] else None


def guard_boundaries(words, proposals, witness):
    if not proposals:
        return []
    labels = [word.get('speaker') for word in words]
    remaining = dict(proposals)
    # A short acknowledgement bounded on both sides can be an entire reply.
    # Keep it even when proposed changes to the neighbouring words would merge it.
    replies = []
    for person, group in groupby(range(len(words)), key=labels.__getitem__):
        run = list(group)
        left, right = run[0], run[-1]
        if (person and left > 0 and right + 1 < len(words) and labels[left-1] and labels[right+1]
                and all(is_reply(words[i]) for i in run) and any(i in remaining for i in run)):
            replies.append(run)

    def assigned(i):
        return remaining[i]['proposed'] if i in remaining else labels[i]

    while remaining:
        count = len(remaining)
        for run in replies:
            if len({assigned(i) for i in range(run[0]-1, run[-1]+2)}) == 1:
                for i in run:
                    remaining.pop(i, None)
        # Assess adjacent proposals for the same person as one change in the transcript.
        runs = groupby(enumerate(sorted(remaining)), key=lambda pair: (
            pair[1] - pair[0], proposals[pair[1]]['proposed']))
        for (_, speaker), group in runs:
            indices = [i for _, i in group]
            left, right = indices[0], indices[-1]
            reject = False
            for inside, outside in ((left, left - 1), (right, right + 1)):
                if not 0 <= outside < len(words):
                    continue
                neighbor = assigned(outside)
                if neighbor and neighbor != speaker and labels[inside] == labels[outside]:
                    if not witness(outside, neighbor):
                        reject = True
            if reject:
                for i in indices:
                    del remaining[i]
        if len(remaining) == count:
            break
    return [remaining[i] for i in sorted(remaining)]


def propose(transcript, mapping, logits, mask, step, encode):
    words = transcript['word_segments']
    diar = transcript['diarization_result']
    if len(set(mapping.values())) != len(mapping) or not len(mask):
        return []
    frames = (np.arange(len(mask)) + .5) * step
    prob = expit(logits)
    solo = np.where(mask & ((prob >= .4).sum(axis=1) == 1), prob.argmax(axis=1), -1)
    strong = np.where(prob.max(axis=1) >= .85, solo, -1)
    cuts = np.r_[0, np.flatnonzero(np.diff(solo)) + 1, len(mask)]
    people = {i: mapping.get(f'speaker_{i}') for i in range(prob.shape[1])}
    channels = {s: i for i, s in people.items() if s is not None}
    pools = reference_samples(diar['regular_segments'], frames, step)
    turns = sorted(diar['segments'], key=lambda t: t['start'])
    starts = [t['start'] for t in turns]
    proposals = {}

    def bounds(i):
        return np.searchsorted(frames, [words[i]['start'], words[i]['end']])

    def witness(i, speaker):
        a, b = bounds(i)
        return (speaker in channels and (b-a) * step >= .075 and mask[a:b].all()
                and (strong[a:b] == channels[speaker]).mean() >= .8)

    for i, word in enumerate(words):
        previous = word.get('speaker')
        if (previous not in pools or word.get('speaker_review') or word['start'] < 0
                or not 0 < word['end'] - word['start'] <= 2 or word['end'] > len(mask) * step + .02):
            continue
        a, b = bounds(i)
        if b <= a or not mask[a:b].all():
            continue
        mid = a + np.argmax(prob[a:b].max(axis=1))
        ch = strong[mid]
        speaker = people.get(ch)
        supporting = strong[a:b] == ch
        if not speaker or speaker == previous or speaker not in pools or supporting.mean() < .8:
            continue
        # Confidence here measures activity over the corroborating frames.
        activity = prob[a:b][supporting].mean(axis=0)
        margin = activity[ch] - np.max(np.delete(activity, ch), initial=0.)
        if activity[ch] < .9 or margin < .25:
            continue
        j = np.searchsorted(cuts, mid, side='right') - 1
        left, right = cuts[j:j+2]
        center = (a+b) / 2
        left, right = max(left, int(center - 1/step)), min(right, int(center + 1/step))
        clips = []
        if (right-left) * step >= .45:
            clips.append((left*step, right*step))
        k = bisect_right(starts, word['start']) - 1
        if k >= 0:
            t = turns[k]
            if (word['end'] <= t['end'] and t['speaker'] == previous
                    and .45 <= t['end'] - t['start'] <= 2):
                clips.append((t['start'], t['end']))
        winners = {compare_clip(x, y, pools, previous, speaker, encode) for x, y in clips}
        if winners - {None} == {speaker}:
            proposals[i] = dict(index=i, word=word['word'], start=word['start'], end=word['end'],
                                previous=previous, proposed=speaker, activity=float(activity[ch]),
                                margin=float(margin))
    return guard_boundaries(words, proposals, witness)


def run(args):
    raw = args.transcript.read_bytes()
    transcript = json.loads(raw)
    batch = json.loads(args.mapping.read_bytes())
    native = json.loads(args.native.read_bytes())
    artifact = native['activity_artifact']
    if file_hash(args.audio) != native['source_sha256']:
        raise ValueError('Audio differs from the waveform used for native activity')
    mapped = Path(batch['native_candidate'])
    if mapped.resolve() != args.native.resolve():
        mapped_native = json.loads(mapped.read_bytes())
        if (mapped_native['activity_artifact']['sha256'] != artifact['sha256']
                or mapped_native['source_sha256'] != native['source_sha256']):
            raise ValueError('Channel mapping belongs to different native activity')
    if file_hash(artifact['path']) != artifact['sha256']:
        raise ValueError('Native activity checksum mismatch')
    with wave.open(str(args.audio)) as reader, np.load(artifact['path'], allow_pickle=False) as f:
        if (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) != (16000, 1, 2):
            raise ValueError('Expected 16 kHz mono PCM')
        step, duration = float(f['frame_seconds']), float(f['audio_duration'])
        if abs(reader.getnframes() / 16000 - duration) > max(.02, step):
            raise ValueError('Native activity and audio clocks differ')
        logits, mask = validate(f['logits'], f['attention_mask'], step, duration)
        changes = propose(transcript, batch['mapping'], logits, mask, step, clip_encoder(reader, args.model))
    result = dict(input_sha256=hashlib.sha256(raw).hexdigest(), proposals=changes)
    encoded = json.dumps(result, allow_nan=False)
    with os.fdopen(os.open(args.out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as stream:
        stream.write(encoded)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('audio', 'transcript', 'native', 'mapping', 'model', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    run(parser.parse_args())
