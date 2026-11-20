"""Recheck unassigned word times on short, original-clock audio excerpts.

Timing evidence only. Never replace the source words, times, or speaker names.
"""
import argparse
from bisect import bisect_left
import hashlib
import json
from pathlib import Path
import wave


def token(word):
    return ''.join(c for c in word.casefold().replace('’', "'") if c.isalnum() or c == "'")


def windows(transcript, duration):
    turns = sorted(transcript['diarization_result']['regular_segments'], key=lambda t: t['start'])
    starts = [t['start'] for t in turns]
    ends = []
    for turn in turns:
        ends.append(max(turn['end'], ends[-1] if ends else 0))
    requests = []
    for i, word in enumerate(transcript['word_segments']):
        if word.get('speaker') or word.get('speaker_review') or not token(word['word']):
            continue
        a, b = word['start'], word['end']
        if not 0 <= a < b <= duration+.02:
            continue
        voices = {t['speaker'] for t in turns[bisect_left(ends, a):bisect_left(starts, b)] if t['end'] > a}
        if len(voices) == 1:
            for t in (a, b):
                requests.append((max(0, t-2), min(duration, t+2), i))
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


def run(audio, transcript):
    import numpy as np
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError
    if transcript.get('language') != 'en':
        return [], 0
    try:
        model = snapshot_download('mlx-community/whisper-large-v3-turbo', local_files_only=True)
    except LocalEntryNotFoundError:
        return [], 0
    import mlx_whisper
    words = transcript['word_segments']
    hits = {}
    with wave.open(str(audio)) as reader:
        if (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) != (16000, 1, 2):
            raise ValueError('Expected mono 16 kHz PCM')
        clips = windows(transcript, reader.getnframes()/16000)
        for a, b, indices in clips:
            start = round(a*16000)
            reader.setpos(start)
            samples = np.frombuffer(reader.readframes(round(b*16000)-start), dtype='<i2').astype(np.float32)/32768
            result = mlx_whisper.transcribe(samples, path_or_hf_repo=model, language='en', word_timestamps=True,
                                            condition_on_previous_text=False, temperature=0., verbose=None)
            decoded = [t for seg in result['segments'] if seg.get('no_speech_prob', 0) < .6
                       for t in seg.get('words', [])]
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
        observed.append(dict(index=i, word=w['word'], start=w['start'], end=w['end'], onset=a, offset=b))
    return observed, len(clips)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('audio','input','out'):
        parser.add_argument('--'+key, type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    observed, count = run(args.audio,json.loads(raw))
    with args.out.open('x') as stream:
        json.dump(dict(input_sha256=hashlib.sha256(raw).hexdigest(), observed=observed, excerpts=count),
                  stream, allow_nan=False)
    args.out.chmod(0o600)
