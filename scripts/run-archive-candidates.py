#!/usr/bin/env python3
"""Resume a sequential, local-only candidate queue without updating Plume.

Each candidate remains unreviewed. Per-recording failures are retained and do not
block unrelated offline work. Resource/queue conflicts stop the worker cleanly.
"""
import argparse
import collections
import dataclasses
import difflib
import fcntl
import json
import math
import os
import re
from pathlib import Path
import shutil
import traceback

from archive_repair import DEFAULT_DB, load_script, now, read_db, sha256, write_json


class PauseWork(Exception):
    pass


def repetition_signals(output):
    """Triage only: genuine speech/music can repeat; never edit or accept text."""
    words = re.findall(r"\b[\w']+\b", output.get('text', '').lower())
    phrase_count = 0
    for offset in range(0, len(words), 80):
        window = words[offset:offset + 200]
        counts = collections.Counter(tuple(window[i:i + 8]) for i in range(max(0, len(window) - 7)))
        phrase_count = max(phrase_count, max(counts.values(), default=0))
    return {
        'high_compression': any(s.get('compression_ratio', 0) > 2.4 for s in output.get('segments', [])),
        'local_phrase_loop': phrase_count >= 3,
    }


def numeric_values_finite(value):
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(numeric_values_finite(v) for v in value.values())
    if isinstance(value, list):
        return all(numeric_values_finite(v) for v in value)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--fallback-on-repetition', action='store_true',
                        help='Save an additional unreviewed retry when the baseline has repetition signals; retain both primary candidates')
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    plan = json.loads(args.plan.read_text())
    if Path(plan['db']).resolve() != args.db.resolve():
        raise ValueError('Plan database differs from worker database')
    path = root / 'manifest.json'
    fingerprint = sha256(args.plan)
    manifest = json.loads(path.read_text()) if path.exists() else {
        'started_at': now(), 'mode': 'offline_candidates_only',
        'plan_sha256': fingerprint, 'jobs': [],
    }
    if manifest['plan_sha256'] != fingerprint:
        raise ValueError('Frozen plan changed; use a new output directory')
    queue = load_script('queue-archive-quality-rerun')
    audit = load_script('build-archive-quality-audit')

    def save():
        manifest['updated_at'] = now()
        write_json(path, manifest)

    def guard():
        if (root / 'STOP').exists():
            raise PauseWork('STOP file present')
        if min(shutil.disk_usage(root).free, shutil.disk_usage(args.db.parent).free) < 12 * 1024**3:
            raise PauseWork('Free space below 12 GiB')
        with read_db(args.db) as db:
            if db.execute("SELECT count(*) FROM transcription_jobs WHERE status IN ('pending','processing')").fetchone()[0]:
                raise PauseWork('Plume is processing a recording; yield local compute')

    lock_dir = args.db.parent  # Shared offline-worker ownership as well as repair lock.
    with (lock_dir / '.archive-candidates.lock').open('a') as owner, (root.parent / 'retranscription-runs/.archive-repair.lock').open('a') as repair:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(repair, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            guard()
            manifest.update(state='loading_model', worker_pid=os.getpid(), optional_fallback_enabled=args.fallback_on_repetition)
            manifest.pop('reason', None)
            save()
            cache = args.db.parent / 'whisperx-env/cache'
            os.environ.update(HF_HOME=str(cache / 'huggingface'),
                              HUGGINGFACE_HUB_CACHE=str(cache / 'huggingface/hub'),
                              TORCH_HOME=str(cache / 'torch'), HF_HUB_OFFLINE='1',
                              TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                              OMP_NUM_THREADS='6')
            import numpy as np
            import torch
            from faster_whisper import WhisperModel
            from faster_whisper.audio import decode_audio
            from faster_whisper.vad import VadOptions
            torch.set_num_threads(6)
            model = WhisperModel('large-v3', device='cpu', compute_type='int8',
                                 cpu_threads=6, local_files_only=True)
            existing = {j['job_id']: j for j in manifest['jobs']}
            for entry in plan['jobs']:
                guard()
                item = existing.get(entry['job_id'])
                if item and item['state'] in ('candidate_unreviewed', 'failed_candidate'):
                    continue
                if item is None:
                    item = {'job_id': entry['job_id'], 'state': 'preparing', 'started_at': now()}
                    manifest['jobs'].append(item)
                manifest.update(state='running', current_job=entry['job_id'])
                save()
                try:
                    target = root / entry['job_id']
                    before_path = target / 'snapshot.before.json'
                    if before_path.exists():
                        before = json.loads(before_path.read_text())
                    else:
                        with read_db(args.db) as db:
                            before, item['backup'] = queue.backup(db, root, entry)
                    source = before['job']['audio_path']
                    if sha256(source) != before['audio_sha256'] or sha256(before['job']['transcript_json_path']) != before['transcript_sha256']:
                        raise ValueError('Original audio or live transcript changed')
                    item['before_metrics'] = before['metrics']
                    audio = decode_audio(source, sampling_rate=16000)
                    if not np.isfinite(audio).all():
                        raise ValueError('Non-finite source audio samples')
                    item['state'] = 'decoding'
                    language = None
                    outputs = {}
                    modes = ['full_audio', 'silero_comparison']
                    for mode in modes:
                        guard()
                        is_retry = mode == 'temperature_fallback'
                        temperature = [0, .2, .4, .6, .8, 1] if is_retry else 0
                        use_vad = mode == 'silero_comparison'
                        try:
                            output_path = target / (mode + '.json')
                            if output_path.exists():
                                output = json.loads(output_path.read_text())
                                if output['metadata']['source_sha256'] != before['audio_sha256']:
                                    raise ValueError('Cached output belongs to a different source')
                            else:
                                segments, info = model.transcribe(
                                    audio, language=outputs['full_audio']['language'] if is_retry else language, beam_size=5, best_of=5,
                                    temperature=temperature, condition_on_previous_text=False,
                                    word_timestamps=True, vad_filter=use_vad,
                                    vad_parameters=VadOptions(threshold=.5, neg_threshold=.363, max_speech_duration_s=30) if use_vad else None,
                                    log_prob_threshold=-.5, no_speech_threshold=.5)
                                result = []
                                for segment in segments:
                                    guard()
                                    result.append(dataclasses.asdict(segment))
                                output = {'language': info.language, 'segments': result,
                                          'text': ' '.join(s['text'] for s in result),
                                          'metadata': {'offline_candidate': True, 'human_reviewed': False,
                                                       'model': 'large-v3', 'compute_type': 'int8',
                                                       'vad_filter': use_vad, 'temperature': temperature,
                                                       'candidate_mode': mode,
                                                       'logprob_threshold': -.5, 'no_speech_threshold': .5,
                                                       'condition_on_previous_text': False,
                                                       'source_sha256': before['audio_sha256']}}
                                if not numeric_values_finite(output):
                                    raise ValueError('Non-finite decoding diagnostics')
                                write_json(output_path, output)
                            outputs[mode] = output
                            if mode == 'full_audio' and args.fallback_on_repetition:
                                signals = repetition_signals(output)
                                item['retry_triage'] = signals
                                if any(signals.values()):
                                    modes.append('temperature_fallback')
                            language = output['language']
                            item[mode + '_metrics'] = audit.parse_transcript(output_path)
                            save()
                        except PauseWork:
                            raise
                        except Exception as exc:
                            if not is_retry:
                                raise
                            item['retry_error'] = str(exc)
                            traceback.print_exc()
                            save()
                    del audio
                    if sha256(source) != before['audio_sha256'] or sha256(before['job']['transcript_json_path']) != before['transcript_sha256']:
                        raise ValueError('Original audio or live transcript changed during decoding')
                    item.update(state='candidate_unreviewed', finished_at=now(),
                                source_unchanged=True, live_transcript_unchanged=True,
                                decoder_similarity=difflib.SequenceMatcher(None, outputs['full_audio']['text'].lower().split(), outputs['silero_comparison']['text'].lower().split(), autojunk=False).ratio())
                    print(entry['job_id'], 'candidate saved', flush=True)
                except PauseWork:
                    raise
                except Exception as exc:
                    item.update(state='failed_candidate', error=str(exc), finished_at=now())
                    traceback.print_exc()
                save()
            manifest.update(state='queue_complete', current_job=None, finished_at=now())
            save()
        except PauseWork as exc:
            manifest.update(state='paused', reason=str(exc))
            save()


if __name__ == '__main__':
    main()
