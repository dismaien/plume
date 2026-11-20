#!/usr/bin/env python3
"""Local Community-1 diarisation with replayable regular/exclusive timelines."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import time
import wave
import warnings
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
                  min_speakers=None, max_speakers=None, output_format='json', device='auto',
                  cpu_threads=None, embedding_exclude_overlap=None):
    import torch
    from pyannote.audio import Pipeline
    started = time.monotonic()
    actual_device = select_device(device, torch)
    if cpu_threads is not None and not 1 <= cpu_threads <= 6:
        raise ValueError('cpu_threads must be between one and six')
    torch.set_num_threads(cpu_threads if cpu_threads is not None else min(6, os.cpu_count() or 1))
    # HF_HUB_OFFLINE=1 works without a token when the model is already cached.
    pipeline = Pipeline.from_pretrained(model, token=hf_token or None)
    if pipeline is None:
        raise RuntimeError('Community-1 unavailable. Cache the model after accepting its terms or configure HF_TOKEN.')
    if embedding_exclude_overlap is not None:
        pipeline.embedding_exclude_overlap = bool(embedding_exclude_overlap)
    pipeline.to(torch.device(actual_device))
    audio = load_audio_waveform(audio_path)
    params = {k: v for k, v in {'min_speakers': min_speakers, 'max_speakers': max_speakers}.items() if v is not None}
    result = pipeline(audio, **params)
    regular = getattr(result, 'speaker_diarization', result)
    exclusive = getattr(result, 'exclusive_speaker_diarization', regular)
    segments = annotation_segments(exclusive)
    regular_segments = annotation_segments(regular)
    raw_speakers = speaker_inventory(regular_segments, segments)
    aliases = {}
    votes = []
    mixed = {}
    check_status = "skipped"
    check_hash = ""
    if not params and model == 'pyannote/speaker-diarization-community-1':
        import speaker_aliases
        check_hash = hashlib.sha256(Path(speaker_aliases.__file__).read_bytes()).hexdigest()

        def sample(start, end):
            rate = audio['sample_rate']
            return audio['waveform'][:, round(start * rate):round(end * rate)], rate
        try:
            aliases, votes = speaker_aliases.check_aliases(segments, regular_segments, sample, pipeline)
            mixed = speaker_aliases.mixed_tracks(votes, aliases, regular_segments)
            check_status = "tested" if votes else "insufficient_reference_turns"
        except (RuntimeError, ValueError) as err:
            # The original diarization succeeded. A failed optional reference
            # panel must not discard its timeline or apply a partial decision.
            aliases, votes, mixed = {}, [], {}
            check_status = "failed_primary_retained"
            warnings.warn(f"Speaker reference check failed: {err}")
        if aliases:
            original = {str(label).lower(): label for label in set(regular.labels()) | set(exclusive.labels())}
            mapping = {original[s]: original[target] for s, target in aliases.items()}
            regular = regular.rename_labels(mapping=mapping)
            exclusive = exclusive.rename_labels(mapping=mapping)
            segments = annotation_segments(exclusive)
            regular_segments = annotation_segments(regular)
    mixed_exclusive = [r for r in segments if r['speaker'] in mixed]
    mixed_regular = [r for r in regular_segments if r['speaker'] in mixed]
    if mixed:
        segments = [r for r in segments if r['speaker'] not in mixed]
        regular_segments = [r for r in regular_segments if r['speaker'] not in mixed]
    speakers = speaker_inventory(regular_segments, segments)
    metadata = {
        'speaker_alias_check_status': check_status, 'speaker_alias_check_sha256': check_hash,
        'speaker_count_before_alias_check': str(len(raw_speakers)),
        'speaker_aliases': json.dumps(aliases, sort_keys=True),
        'speaker_mixed_tracks': json.dumps(mixed, sort_keys=True),
        'speaker_alias_panel_votes': json.dumps(votes, sort_keys=True),
        'diarization_engine': model, 'diarization_device': actual_device,
        'pyannote_audio_version': importlib.metadata.version('pyannote.audio'),
        'torch_version': torch.__version__, 'speaker_identity_status': ('solo' if len(speakers)==1 else 'accepted') if speakers else 'uncertain',
        'torch_cpu_threads': str(torch.get_num_threads()),
        'embedding_exclude_overlap': str(pipeline.embedding_exclude_overlap).lower(),
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
    if mixed:
        metadata.update(speaker_identity_status='uncertain',
                        speaker_identity_reason='mixed_track_needs_turn_review')
    payload = {'audio_file': str(audio_path), 'model': model, 'segments': segments,
               'regular_segments': regular_segments, 'speakers': speakers,
               'speaker_count': len(speakers), 'metadata': metadata}
    if mixed:
        payload.update(mixed_segments=mixed_exclusive, mixed_regular_segments=mixed_regular)
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
            # RTTM has no review-track field. Retain the timed evidence under
            # an explicitly mixed track name instead of claiming another person.
            regular.rename_labels(mapping={s: 'MIXED_' + str(s) for s in regular.labels()
                                           if str(s).lower() in mixed}).write_rttm(handle)
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
