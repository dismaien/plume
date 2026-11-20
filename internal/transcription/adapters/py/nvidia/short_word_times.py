"""Recheck unassigned word times on short, original-clock audio excerpts.

Timing evidence only. Never replace the source words, times, or speaker names.
"""
import argparse
from bisect import bisect_left
import hashlib
import json
import time
from pathlib import Path
from typing import NamedTuple

import numpy as np
import wave


def token(word):
    return ''.join(c for c in word.casefold().replace('’', "'") if c.isalnum() or c == "'")


class NativeWordClock(NamedTuple):
    speakers: list
    frames: np.ndarray
    confirmed: np.ndarray
    disputed: np.ndarray
    channels: np.ndarray
    step: float


def native_word_clock(transcript, native_path):
    """Locate locally identified solo speech corroborated on both primary timelines."""
    from activity_timeline import file_hash, validate
    from gap_evidence import local_voice_evidence

    artifact = json.loads(native_path.read_bytes())['activity_artifact']
    path = Path(artifact['path'])
    if file_hash(path) != artifact['sha256']:
        raise ValueError('Native frame artifact changed')
    with np.load(path, allow_pickle=False) as data:
        step = float(data['frame_seconds'])
        logits, mask = validate(data['logits'], data['attention_mask'], step, float(data['audio_duration']))
        probabilities = 1 / (1 + np.exp(-np.clip(logits.astype(np.float32, copy=False), -60., 60.)))
    evidence = local_voice_evidence(transcript['diarization_result'], probabilities, mask, step)
    if evidence is None:
        return None
    people, frames, exclusive, regular, active, solo, local = evidence
    top = probabilities.max(axis=1)
    clean = (solo >= 0) & (local >= 0) & (local == exclusive) & (local == regular) & (top >= .85)
    # Keep contradictory and overlapping activity as explicit vetoes.
    disputed = (~mask | (active.sum(axis=1) > 1) | (regular == -2)
                | ((exclusive >= 0) & (local >= 0) & (exclusive != local))
                | ((regular >= 0) & (local >= 0) & (regular != local)))
    return NativeWordClock(people, frames, np.where(clean, local, -1), disputed, solo, step)


def native_witness(clock, start, end):
    if clock is None:
        return None
    a, b = np.searchsorted(clock.frames, [start, end])
    if b <= a or clock.disputed[a:b].any():
        return None
    channels = clock.channels[a:b]
    voiced = channels[channels >= 0]
    if not len(voiced) or len(voiced) < .4*(b-a) or np.any(voiced != voiced[0]):
        return None
    ids = clock.confirmed[a:b]
    known = ids[ids >= 0]
    # A decoded token can include a silent lead-in. Measure agreement over
    # the frames where the independent model actually detected speech.
    if len(known)*clock.step < .075 or len(known) < .8*len(voiced) or np.any(known != known[0]):
        return None
    return clock.speakers[known[0]]


def windows(transcript, duration, clock=None):
    turns = sorted(transcript['diarization_result']['regular_segments'], key=lambda t: t['start'])
    starts = [t['start'] for t in turns]
    ends = []
    for turn in turns:
        ends.append(max(turn['end'], ends[-1] if ends else 0))
    original, supplemental = [], []
    for i, word in enumerate(transcript['word_segments']):
        if word.get('speaker') or word.get('speaker_review') or not token(word['word']):
            continue
        a, b = word['start'], word['end']
        if not 0 <= a < b <= duration+.02:
            continue
        voices = {t['speaker'] for t in turns[bisect_left(ends, a):bisect_left(starts, b)] if t['end'] > a}
        for t in (a, b):
            request = (max(0, t-2), min(duration, t+2), i)
            if len(voices) == 1:
                original.append(request)
            elif clock is not None:
                lo, hi = np.searchsorted(clock.frames, [t-.75, t+.75])
                if np.count_nonzero(clock.confirmed[lo:hi] >= 0) * clock.step >= .075:
                    supplemental.append(request)
    # Adding new targets must not change the context decoded for existing ones.
    return group_clips(original) + group_clips(supplemental)


def group_clips(requests):
    clips = []
    for start, end, i in sorted(requests):
        if clips and start <= clips[-1][1] and end-clips[-1][0] <= 12:
            clips[-1][1] = max(clips[-1][1], end)
            clips[-1][2].add(i)
        else:
            clips.append([start,end,{i}])
    return clips


def matches(words, index, decoded, offset):
    """Bind a second decode to the same token occurrence and a timed neighbour."""
    w = words[index]
    found = []
    for j, item in enumerate(decoded):
        a, b = offset+item['start'], offset+item['end']
        if (token(item['word']) != token(w['word']) or item.get('probability', 0) < .8
                or not 0 < b-a <= 1.5 or b < w['start']-.2 or a > w['end']+.2
                or min(abs(b-w['end']), abs(a-w['start'])) > .8):
            continue
        for k in (-1,1):
            if not (0 <= index+k < len(words) and 0 <= j+k < len(decoded)):
                continue
            other, anchor = words[index+k], decoded[j+k]
            if (token(other['word']) == token(anchor['word']) and token(other['word']) != token(w['word'])
                    and anchor.get('probability',0) >= .65
                    and offset+anchor['end'] >= other['start']-.25 and offset+anchor['start'] <= other['end']+.25
                    and min(abs(other['start']-offset-anchor['start']), abs(other['end']-offset-anchor['end'])) <= .65):
                found.append((a,b))
                break
    return found


def run(audio, transcript, native_path=None):
    started = time.monotonic()
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError
    language = transcript.get('language')
    if language not in ('en', 'de'):
        return [], 0
    try:
        model = snapshot_download('mlx-community/whisper-large-v3-turbo', local_files_only=True)
    except LocalEntryNotFoundError:
        return [], 0
    import mlx_whisper
    words = transcript['word_segments']
    clock = native_word_clock(transcript, native_path) if native_path else None
    hits = {}
    with wave.open(str(audio)) as reader:
        if (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) != (16000, 1, 2):
            raise ValueError('Expected mono 16 kHz PCM')
        duration = reader.getnframes()/16000
        clips = windows(transcript, duration, clock)
        original_count = len(windows(transcript, duration)) if clock is not None else len(clips)
        checked = 0
        for a, b, indices in clips:
            # Finish with verified observations before the caller's 15-minute
            # deadline when a long recording offers many extra handoff checks.
            if checked >= original_count and time.monotonic()-started >= 12*60:
                break
            start = round(a*16000)
            reader.setpos(start)
            samples = np.frombuffer(reader.readframes(round(b*16000)-start), dtype='<i2').astype(np.float32)/32768
            result = mlx_whisper.transcribe(samples, path_or_hf_repo=model, language=language, word_timestamps=True,
                                            condition_on_previous_text=False, temperature=0., verbose=None)
            decoded = [t for seg in result['segments'] if seg.get('no_speech_prob', 0) < .6
                       for t in seg.get('words', [])]
            checked += 1
            for i in indices:
                hits.setdefault(i, []).extend(matches(words, i, decoded, start/16000))
    observed = []
    for i, times in sorted(hits.items()):
        if not times:
            continue
        a, b = times[0]
        if any(abs(a-x) > .10 or abs(b-y) > .10 for x,y in times):
            continue
        w = words[i]
        observed.append(dict(index=i, word=w['word'], start=w['start'], end=w['end'], onset=a, offset=b, native_speaker=native_witness(clock, a, b)))
    return observed, checked


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('audio','input','out'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--native', type=Path)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    observed, count = run(args.audio,json.loads(raw),args.native)
    with args.out.open('x') as stream:
        json.dump(dict(input_sha256=hashlib.sha256(raw).hexdigest(), observed=observed, excerpts=count),
                  stream, allow_nan=False)
    args.out.chmod(0o600)
