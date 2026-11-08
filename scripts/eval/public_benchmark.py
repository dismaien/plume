#!/usr/bin/env python3
"""Local regression benchmark on pinned AMI manual annotations.

No credentials, uploads, live database access or managed-runtime modifications.
Four deliberately fixed tuning excerpts from ONE public meeting are a smoke
benchmark, not held-out proof of quality on personal voice memos.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import wave

from score_reference import digest, score

ANNOTATION_COMMIT = '67c2d539286e89f68952d5dcf83912bd9f01dfae'
AUDIO_URL = 'https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/IS1009a/audio/IS1009a.Mix-Headset.wav'
AUDIO_HASH = '6eb5a0ede0d9e72794f976ce7bea5b78133eae969f99b4c5418b43c2468d25b1'
RTTM_URL = f'https://raw.githubusercontent.com/pyannote/AMI-diarization-setup/{ANNOTATION_COMMIT}/only_words/rttms/test/IS1009a.rttm'
RTTM_HASH = 'ba38d35ca567f3f1e061d90fdc33579ce60d2060fc8664eaeb77d2e0fcd88b01'
EXCERPTS = [('two-speakers', 120, 180), ('four-opening', 0, 180),
            ('four-middle', 300, 480), ('four-brief-speaker', 600, 780)]


def fetch_sources(source):
    source.mkdir(parents=True, exist_ok=True)
    for name, url, expected in [('IS1009a.Mix-Headset.wav', AUDIO_URL, AUDIO_HASH),
                                 ('IS1009a.rttm', RTTM_URL, RTTM_HASH)]:
        path = source / name
        if path.exists():
            if digest(path) != expected:
                raise ValueError(f'Existing source hash mismatch: {name}')
            continue
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(128 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f'Downloaded source hash mismatch: {name}')
        with path.open('xb') as handle:
            handle.write(data)


def prepare(source, out):
    """Create references exclusively from the published manual-annotation RTTM."""
    out.mkdir(parents=True, exist_ok=False)
    audio = source / 'IS1009a.Mix-Headset.wav'
    if digest(audio) != AUDIO_HASH:
        raise ValueError('AMI source waveform hash mismatch')
    rttm = source / 'IS1009a.rttm'
    canonical = rttm.read_bytes()
    if digest(rttm) != RTTM_HASH:
        raise ValueError('AMI annotation does not match pinned source')
    turns = []
    for line in canonical.decode().splitlines():
        f = line.split()
        turns.append((float(f[3]), float(f[3]) + float(f[4]), f[7]))
    for name, start, end in EXCERPTS:
        directory = out / name
        directory.mkdir()
        with wave.open(str(audio)) as wav:
            params = wav.getparams()
            wav.setpos(start * wav.getframerate())
            data = wav.readframes((end - start) * wav.getframerate())
        with wave.open(str(directory / 'audio.wav'), 'wb') as wav:
            wav.setparams(params)
            wav.writeframes(data)
        rows = []
        for s, e, speaker in turns:
            s, e = max(start, s), min(end, e)
            if e > s:
                rows.append(f'SPEAKER {name} 1 {s-start:.6f} {e-s:.6f} <NA> <NA> {speaker} <NA> <NA>')
        (directory / 'reference.rttm').write_text('\n'.join(rows) + '\n')
        (directory / 'reference.uem').write_text(f'{name} 1 0 {end-start}\n')
        manifest = dict(recording_id=name, audio='audio.wav', source_sha256=digest(directory / 'audio.wav'),
                        rttm='reference.rttm', uem='reference.uem', split='tuning',
                        reviewed_by_human=True,
                        reviewer='AMI corpus manual annotators v1.6.2; published BUT/pyannote RTTM derivation',
                        annotation_origin='published_manual_annotations_not_reviewed_by_Codex',
                        annotation_url=RTTM_URL, annotation_sha256=digest(rttm),
                        original_audio_url=AUDIO_URL, original_audio_sha256=AUDIO_HASH,
                        excerpt_start=start, excerpt_end=end,
                        limitation='One public meeting, known benchmark; not held-out Plume accuracy evidence')
        (directory / 'reference.json').write_text(json.dumps(manifest, indent=2))


def benchmark(args):
    root = Path(__file__).resolve().parents[2]
    if args.fetch_source:
        fetch_sources(args.source)
    prepare(args.source, args.out)
    cache = args.cache.resolve()
    env = dict(os.environ, HF_HOME=str(cache), HUGGINGFACE_HUB_CACHE=str(cache / 'hub'),
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYANNOTE_METRICS_ENABLED='0',
               HF_HUB_DISABLE_TELEMETRY='1', PYTORCH_ENABLE_MPS_FALLBACK='1',
               OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4', MKL_NUM_THREADS='4',
               VIRTUAL_ENV=str(args.nemo_python.absolute().parents[1]))
    # Inference must not inherit cloud or Hugging Face credentials.
    for key in list(env):
        if key in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN') or key.endswith('API_KEY'):
            env.pop(key)
    results = []
    for name, _, _ in EXCERPTS:
        directory = args.out / name
        audio = directory / 'audio.wav'
        commands = [
            ('community1-auto', [str(args.community_python), str(root / 'internal/transcription/adapters/py/pyannote/pyannote_diarize.py'),
                                 str(audio), '--output', str(directory / 'community1-auto.json'), '--device', args.device]),
            ('sortformer-heads', [str(args.nemo_python), str(root / 'internal/transcription/adapters/py/nvidia/sortformer_diarize.py'),
                                  str(audio), str(directory / 'sortformer-heads.json'), '--output-format', 'json', '--device', 'cpu', '--raw-heads']),
            ('sortformer-recluster', [str(args.nemo_python), str(root / 'internal/transcription/adapters/py/nvidia/recluster_speakers.py'),
                                      str(audio), str(directory / 'sortformer-heads.json'), str(directory / 'sortformer-recluster.json'), '--max-identities', '4']),
        ]
        for engine, command in commands:
            started = time.monotonic()
            script_hash = digest(command[1])
            with (directory / f'{engine}.log').open('w') as log:
                process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=900)
            receipt = dict(excerpt=name, engine=engine, seconds=time.monotonic()-started,
                           exit_code=process.returncode, source_sha256=digest(audio),
                           stage_script_sha256=script_hash, command=command,
                           production_identity_result=engine != 'sortformer-heads')
            candidate = directory / f'{engine}.json'
            if process.returncode == 0 and candidate.exists():
                measured = score(directory / 'reference.json', candidate)
                (directory / f'{engine}-score.json').write_text(json.dumps(measured, indent=2, allow_nan=False))
                receipt['diarization'] = measured['diarization']
                receipt['candidate_sha256'] = digest(candidate)
                payload = json.loads(candidate.read_text())
                receipt['declared_speaker_count'] = payload.get('speaker_count', payload.get('identity_count'))
                receipt['exclusive_speaker_count'] = len({t['speaker'] for t in payload.get('segments', []) if t.get('speaker')})
                receipt['regular_speaker_count'] = len({t['speaker'] for t in payload.get('regular_segments', []) if t.get('speaker')})
            else:
                receipt['error'] = 'Inference failed; inspect stage log'
            results.append(receipt)
            (args.out / 'results.json').write_text(json.dumps(dict(
                benchmark='AMI IS1009a fixed tuning smoke excerpts', results=results,
                limitations=['single public meeting', 'not held out', 'no private memo accuracy estimate',
                             'raw Sortformer heads are a diagnostic, not stable people']), indent=2, allow_nan=False))
            print(name, engine, process.returncode, round(receipt['seconds'], 2), flush=True)
    return int(any(r['exit_code'] or r.get('error') for r in results))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Directory with the original downloaded AMI audio and RTTM')
    parser.add_argument('--fetch-source', action='store_true', help='Download the pinned public source files if absent')
    parser.add_argument('--out', type=Path, required=True, help='New, private output directory')
    parser.add_argument('--community-python', type=Path, required=True)
    parser.add_argument('--nemo-python', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--device', choices=['auto', 'cpu', 'mps', 'cuda'], default='auto')
    raise SystemExit(benchmark(parser.parse_args()))
