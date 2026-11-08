#!/usr/bin/env python3
"""Pinned local Nemotron 3 inference core; callers own scheduling.

Uses the official native Transformers implementation with cached safetensors.
No remote code, runtime installation, network inference or live DB writes.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time
import wave

from activity_timeline import save_activity


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


def infer(args, before_model=None):
    if args.out.exists() or args.out.with_suffix('.activity.npz').exists():
        raise FileExistsError('Retain existing immutable inference artifacts')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    import numpy as np
    import torch
    import transformers
    from transformers import AutoModelForAudioFrameClassification, AutoProcessor
    started = time.monotonic()
    torch.set_num_threads(getattr(args,'cpu_threads',1))
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
    if before_model is not None:before_model()
    processor = AutoProcessor.from_pretrained(str(args.model), local_files_only=True, trust_remote_code=False)
    model = AutoModelForAudioFrameClassification.from_pretrained(str(args.model), local_files_only=True, trust_remote_code=False)
    model.to(torch.device(args.device)); model.eval()
    inputs = processor(audio,sampling_rate=16000).to(model.device,dtype=model.dtype)
    with torch.inference_mode():
        logits = model(**inputs).logits
    activity=save_activity(args.out.with_suffix('.activity.npz'),logits[0].detach().float().cpu().numpy(),
                           inputs.attention_mask[0].detach().cpu().numpy(),
                           processor.feature_extractor.hop_length/processor.feature_extractor.sampling_rate,
                           len(audio)/16000)
    turns = canonical_segments(processor.extract_speaker_dict(logits,inputs.attention_mask,threshold=.5)[0], len(audio)/16000)
    speakers=sorted({turn['speaker'] for turn in turns})
    payload=dict(model=provenance['model'],model_used=provenance['model'],segments=turns,regular_segments=turns,
                 source_sha256=hashlib.sha256(args.audio.read_bytes()).hexdigest(),activity_artifact=activity,
                 speakers=speakers,speaker_count=len(speakers),metadata={
                     'model_revision':provenance['revision'],'device':args.device,
                     'torch_version':torch.__version__,'transformers_version':transformers.__version__,
                     'torch_cpu_threads':str(torch.get_num_threads()),
                     'frame_activity_threshold':'.5','random_seed':'0',
                     'speaker_identity_status':'accepted' if speakers else 'uncertain',
                     'confidence_kind':'unavailable; frame activity is not person-identity confidence',
                     'assignment_timeline':'regular_native_tracks','processing_seconds':str(time.monotonic()-started),
                     'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                     'experimental':'true; not a production adapter or engine default',
                     'scheduling':getattr(args,'scheduling','Plume supplemental word refinement'),
                 })
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x') as stream:
        json.dump(payload,stream,indent=2,allow_nan=False)
    args.out.chmod(0o600)
    print(f'Nemotron 3 on {args.device}: {len(speakers)} speakers, {len(turns)} regular turns')
