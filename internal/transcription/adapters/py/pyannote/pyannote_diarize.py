#!/usr/bin/env python3
"""Local Community-1 diarisation with replayable regular/exclusive timelines."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import time
import wave
from pathlib import Path


def load_audio_waveform(audio_path):
    import numpy as np
    import torch
    with wave.open(str(audio_path), 'rb') as wav:
        if wav.getsampwidth() != 2:
            raise ValueError('Expected 16-bit PCM WAV input')
        sample_rate, channels = wav.getframerate(), wav.getnchannels()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
    waveform = samples.astype(np.float32).reshape(-1, channels).T.copy() / 32768.0
    return {'waveform': torch.from_numpy(waveform), 'sample_rate': sample_rate}


def select_device(requested, torch):
    if requested == 'auto':
        if torch.cuda.is_available():
            return 'cuda'
        if torch.backends.mps.is_available():
            return 'mps'
        return 'cpu'
    if requested == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA requested but unavailable')
    if requested == 'mps' and not torch.backends.mps.is_available():
        raise ValueError('MPS requested but unavailable')
    return requested


def annotation_segments(annotation):
    return sorted([{'start': float(turn.start), 'end': float(turn.end),
                    'speaker': str(speaker).lower(), 'duration': float(turn.duration)}
                   for turn, _, speaker in annotation.itertracks(yield_label=True)],
                  key=lambda t: (t['start'], t['end'], t['speaker']))


def speaker_inventory(regular_segments, exclusive_segments):
    # Exclusive diarization can omit a person who only speaks during overlap.
    return sorted({s['speaker'] for s in regular_segments + exclusive_segments if s.get('speaker')})


def diarize_audio(audio_path, output_file, hf_token='',
                  model='pyannote/speaker-diarization-community-1',
                  min_speakers=None, max_speakers=None, output_format='json', device='auto'):
    import torch
    from pyannote.audio import Pipeline
    started = time.monotonic()
    actual_device = select_device(device, torch)
    torch.set_num_threads(min(6, os.cpu_count() or 1))
    # HF_HUB_OFFLINE=1 works without a token when the model is already cached.
    pipeline = Pipeline.from_pretrained(model, token=hf_token or None)
    if pipeline is None:
        raise RuntimeError('Community-1 unavailable. Cache the model after accepting its terms or configure HF_TOKEN.')
    pipeline.to(torch.device(actual_device))
    audio = load_audio_waveform(audio_path)
    params = {k: v for k, v in {'min_speakers': min_speakers, 'max_speakers': max_speakers}.items() if v is not None}
    result = pipeline(audio, **params)
    regular = getattr(result, 'speaker_diarization', result)
    exclusive = getattr(result, 'exclusive_speaker_diarization', regular)
    segments = annotation_segments(exclusive)
    regular_segments = annotation_segments(regular)
    speakers = speaker_inventory(regular_segments, segments)
    metadata = {
        'diarization_engine': model, 'diarization_device': actual_device,
        'pyannote_audio_version': importlib.metadata.version('pyannote.audio'),
        'torch_version': torch.__version__, 'speaker_identity_status': ('solo' if len(speakers)==1 else 'accepted') if speakers else 'uncertain',
        'speaker_identity_reason': '' if speakers else 'no_speech_tracks_not_verified_silence',
        'confidence_kind': 'unavailable', 'assignment_timeline': 'exclusive',
        'speaker_count_source': 'regular_and_exclusive_tracks',
        'exclusive_speaker_count': str(len(speaker_inventory([], segments))),
        'regular_speaker_count': str(len(speaker_inventory(regular_segments, []))),
        'speaker_constraints': json.dumps(params, sort_keys=True),
        'processing_seconds': str(time.monotonic() - started),
        'diarization_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'mps_fallback_enabled': os.environ.get('PYTORCH_ENABLE_MPS_FALLBACK','0'),
    }
    payload = {'audio_file': str(audio_path), 'model': model, 'segments': segments,
               'regular_segments': regular_segments, 'speakers': speakers,
               'speaker_count': len(speakers), 'metadata': metadata}
    try:
        from huggingface_hub import hf_hub_download
        checkpoint=Path(hf_hub_download(model,'config.yaml',local_files_only=True))
        metadata['model_snapshot_revision']=checkpoint.parent.name
        metadata['model_config_sha256']=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    except Exception:
        metadata['model_snapshot_revision']='unrecorded'
    path = Path(output_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == 'rttm':
        with path.open('w') as handle:
            regular.write_rttm(handle)
    else:
        path.write_text(json.dumps(payload, indent=2, allow_nan=False))
    print(f'Community-1 completed on {actual_device}: {len(speakers)} speakers, {len(segments)} turns')
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio_file')
    parser.add_argument('--output', '-o', required=True)
    parser.add_argument('--hf-token', default=os.environ.get('HF_TOKEN', ''))
    parser.add_argument('--model', default='pyannote/speaker-diarization-community-1')
    parser.add_argument('--min-speakers', type=int)
    parser.add_argument('--max-speakers', type=int)
    parser.add_argument('--output-format', choices=['json', 'rttm'], default='json')
    parser.add_argument('--device', choices=['auto', 'cpu', 'cuda', 'mps'], default='auto')
    args = parser.parse_args()
    if any(n is not None and n < 1 for n in [args.min_speakers, args.max_speakers]):
        parser.error('speaker counts must be positive')
    if args.min_speakers and args.max_speakers and args.min_speakers > args.max_speakers:
        parser.error('min_speakers cannot exceed max_speakers')
    diarize_audio(args.audio_file, args.output, args.hf_token, args.model,
                  args.min_speakers, args.max_speakers, args.output_format, args.device)


if __name__ == '__main__':
    main()
