#!/usr/bin/env python3
"""Isolated local Nemotron 3 benchmark; never a production fallback.

Uses the official native Transformers implementation with cached safetensors.
No remote code, runtime installation, network inference or live DB writes.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import wave


def check_idle(database):
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as db:
        active = db.execute("select count(*) from transcription_jobs where status in ('pending','processing')").fetchone()[0]
    if active:
        raise RuntimeError('Foreground Plume queue is active; do not load a benchmark model')


def canonical_segments(segments, duration):
    result = []
    for segment in segments:
        start, end = float(segment['Start']), float(segment['End'])
        if not math.isfinite(start+end) or end <= start or start < 0:
            raise ValueError('Invalid model timeline')
        if start >= duration:
            continue
        result.append(dict(start=start, end=min(end,duration), speaker=f"speaker_{int(segment['Speaker'])}"))
    return sorted(result, key=lambda s:(s['start'],s['end'],s['speaker']))


def run(args):
    check_idle(args.db)
    import numpy as np
    import torch
    import transformers
    from transformers import AutoModelForAudioFrameClassification, AutoProcessor
    started = time.monotonic()
    torch.set_num_threads(4)
    torch.manual_seed(0)
    np.random.seed(0)
    if args.device == 'mps' and not torch.backends.mps.is_available():
        raise RuntimeError('MPS explicitly requested but unavailable; no silent device fallback')
    with wave.open(str(args.audio)) as wav:
        if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate()) != (1,2,16000):
            raise ValueError('Expected mono 16-bit PCM at 16kHz')
        audio = np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').astype(np.float32)/32768
    provenance = json.loads((args.model/'provenance.json').read_text())
    for filename, expected in provenance['files'].items():
        with (args.model/filename).open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=expected:
                raise ValueError('Cached model artifact hash mismatch')
    check_idle(args.db)
    processor = AutoProcessor.from_pretrained(str(args.model), local_files_only=True, trust_remote_code=False)
    model = AutoModelForAudioFrameClassification.from_pretrained(str(args.model), local_files_only=True, trust_remote_code=False)
    model.to(torch.device(args.device)); model.eval()
    inputs = processor(audio,sampling_rate=16000).to(model.device,dtype=model.dtype)
    with torch.inference_mode():
        logits = model(**inputs).logits
    turns = canonical_segments(processor.extract_speaker_dict(logits,inputs.attention_mask,threshold=.5)[0], len(audio)/16000)
    speakers=sorted({turn['speaker'] for turn in turns})
    payload=dict(model=provenance['model'],model_used=provenance['model'],segments=turns,regular_segments=turns,
                 speakers=speakers,speaker_count=len(speakers),metadata={
                     'model_revision':provenance['revision'],'device':args.device,
                     'torch_version':torch.__version__,'transformers_version':transformers.__version__,
                     'frame_activity_threshold':'.5','random_seed':'0',
                     'speaker_identity_status':'accepted' if speakers else 'uncertain',
                     'confidence_kind':'unavailable; frame activity is not person-identity confidence',
                     'assignment_timeline':'regular_native_tracks','processing_seconds':str(time.monotonic()-started),
                     'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                     'experimental':'true; not a production adapter or engine default',
                 })
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x') as stream:
        json.dump(payload,stream,indent=2,allow_nan=False)
    args.out.chmod(0o600)
    print(f'Nemotron 3 on {args.device}: {len(speakers)} speakers, {len(turns)} regular turns')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--device',choices=['cpu','mps'],default='mps')
    run(parser.parse_args())
