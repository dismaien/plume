#!/usr/bin/env python3
"""Preview an audit-driven queue; explicitly apply a frozen plan sequentially.

Every selected recording is revalidated against the live database, backed up,
then run alone. Any non-accepted result stops the plan. There is no implicit
retry after interruption or an uncertain POST response.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

from archive_repair import (DATA, DEFAULT_DB, DEFAULT_LEDGER, Ledger, audit_rows,
                            evaluate, load_script, local_request, now, read_db,
                            sha256, snapshot, trusted, write_json)


def select(rows, ledger, args, evidence):
    eligible = []
    for r in rows:
        if r['severity'] not in args.severity or args.category not in r['categories'].split(', '):
            continue
        if args.model and r['model'] != args.model or args.diarizer and r['diarize_model'] != args.diarizer:
            continue
        protected = int(r['human_speaker_mappings']) > 0
        if args.human == 'exclude' and protected or args.human == 'only' and not protected:
            continue
        history = ledger.history(r['job_id'])
        if ledger.state(r['job_id']) in ('accepted', 'queued', 'improved_review', 'rejected'):
            continue
        if args.prior_run and not any(args.prior_run in h['run_dir'] for h in history):
            continue
        if args.verdict and ledger.state(r['job_id']) != args.verdict:
            continue
        duration = float(r['duration_seconds'])
        if duration <= 0 or duration > args.max_duration_hours * 3600:
            continue
        ev = evidence.get(r['job_id'], {})
        expected = int(ev.get('expected_speakers', 0))
        if args.category == 'speakers':
            known = expected or int(r['known_people_count'])
            if not 2 <= known <= 4 or not (int(r['speaker_count']) < known or float(r['dominant_speaker_ratio']) >= .90):
                continue
            if args.verified_speakers_only and not expected and not 2 <= len(r.get('trusted_names', [])) <= 4:
                continue
        entry = dict(r)
        entry.update(kind=args.category, previous_attempts=len(history), prior_runs=[h['run_dir'] for h in history],
                     latest_verdict=history[-1]['state'] if history else 'not_attempted',
                     expected_speakers=expected, speaker_evidence=ev,
                     speaker_evidence_status='reviewed' if expected else 'trusted_mappings' if protected else 'unverified_title' if r['known_people'] else 'none',
                     block_reason='Protected mappings need reconciliation' if protected else '' if r['audio_exists'].lower() == 'true' else 'Missing audio')
        # The initial text pipeline uses its verified four-speaker ceiling, without
        # claiming that four people are present. Speaker repairs need reviewed evidence.
        entry['max_speakers'] = expected or 4
        eligible.append(entry)
    eligible.sort(key=lambda r: (-load_script('build-archive-quality-audit').SEVERITY_RANK[r['severity']],
                                  -int(r['risk_score']), float(r['duration_seconds']), r['job_id']))
    selected, hours = [], 0
    for r in eligible:
        duration = float(r['duration_seconds']) / 3600
        if len(selected) >= args.limit:
            break
        if hours + duration > args.max_hours:
            continue
        selected.append(r)
        hours += duration
    return selected, hours


def build_plan(args):
    with ExitStack() as stack:
        return _build_plan(args, stack)


def _build_plan(args, stack):
    rows = audit_rows(args.audit)
    with read_db(args.db) as conn:
        live = {r[0] for r in conn.execute('SELECT id FROM transcription_jobs WHERE deleted_at IS NULL AND coalesce(is_source_part,0)=0')}
        names = {}
        for mapping in conn.execute('SELECT * FROM speaker_mappings'):
            if trusted(dict(mapping)):
                names.setdefault(mapping['transcription_job_id'], set()).add(mapping['custom_name'])
        for r in rows:
            r['trusted_names'] = sorted(names.get(r['job_id'], set()))
    if live != {r['job_id'] for r in rows}:
        raise ValueError('Audit parent IDs differ from live DB; regenerate audit')
    ledger = stack.enter_context(Ledger(args.ledger))
    ledger.sync(rows)
    ledger.import_legacy(args.runs_dir)
    evidence = json.loads(args.speaker_evidence.read_text()) if args.speaker_evidence else {}
    jobs, hours = select(rows, ledger, args, evidence)
    with read_db(args.db) as conn:
        for job in jobs:
            before = snapshot(conn, job['job_id'])
            job['before_fingerprint'] = before['transcript_sha256']
            job['before_updated_at'] = before['job']['updated_at']
            job['trusted_names'] = sorted({m['custom_name'] for m in before['mappings'] if trusted(m)})
            if before['job'].get('audio_path') and Path(before['job']['audio_path']).is_file():
                measured = probe_duration(before['job']['audio_path'])
                job['audio_duration_seconds'] = measured
                if measured > args.max_duration_hours * 3600:
                    job['block_reason'] = 'Actual source audio exceeds individual duration cap'
            else:
                job['audio_duration_seconds'] = None
    hours = sum(float(j.get('audio_duration_seconds') or j['duration_seconds']) for j in jobs) / 3600
    if hours > args.max_hours:
        raise ValueError('Actual audio exceeds batch cap; reduce selection limit')
    plan = {'schema_version': 2, 'created_at': now(), 'dry_run': True, 'db': str(args.db.resolve()),
            'ledger': str(args.ledger.resolve()), 'audit': str(args.audit.resolve()), 'audit_sha256': sha256(args.audit),
            'selected_audio_hours': hours, 'jobs': jobs}
    write_json(args.output, plan)
    print(json.dumps({'plan': str(args.output), 'selected': len(jobs), 'audio_hours': round(hours, 3)}, indent=2))
    for i, r in enumerate(jobs, 1):
        print(f"{i}\t{r['job_id']}\t{float(r['duration_seconds'])/60:.1f}m\t{r['model']}\t{r['title']}\t{r['flags']}\t{r['speaker_evidence_status']}\t{r['previous_attempts']} attempts\t{r['block_reason']}")


def probe_duration(path):
    result = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(path)], capture_output=True, text=True, check=True, timeout=30)
    duration = float(json.loads(result.stdout)['format']['duration'])
    import math
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Invalid source audio duration')
    return duration


def backup(conn, run_dir, entry):
    before = snapshot(conn, entry['job_id'])
    job = before['job']
    if not before['metrics'] or not before['metrics']['word_count']:
        raise ValueError('Unreadable/empty baseline; needs availability investigation')
    if before['transcript_sha256'] != entry['before_fingerprint'] or job['updated_at'] != entry['before_updated_at']:
        raise ValueError('Plan is stale: transcript or metadata changed')
    if job['status'] != 'completed' or job.get('deleted_at') or job.get('is_source_part'):
        raise ValueError('Job is not an eligible completed parent')
    if any(trusted(m) for m in before['mappings']):
        raise ValueError('Protected mappings require reviewed reconciliation; in-place execution blocked')
    job_dir = run_dir / job['id']
    job_dir.mkdir(exist_ok=False)
    paths = {}
    for column, filename in [('transcript_json_path', 'transcript.before.json'), ('transcript_markdown_path', 'transcript.before.md')]:
        source = Path(job.get(column) or '')
        if not source.is_file():
            raise ValueError('Missing baseline artifact: ' + column)
        target = job_dir / filename
        shutil.copy2(source, target)
        if sha256(source) != sha256(target):
            raise ValueError('Backup integrity mismatch')
        paths['transcript_json_backup' if column.endswith('json_path') else 'transcript_markdown_backup'] = str(target)
    sidecars = {}
    artifact_dir = Path(job.get('artifact_dir') or Path(job['transcript_json_path']).parent)
    for name in ('metadata.json','transcript.txt','transcript.srt','transcript.vtt'):
        source = artifact_dir / name
        if source.is_file():
            target = job_dir / ('artifact.before.' + name)
            shutil.copy2(source, target)
            sidecars[name] = {'backup': str(target), 'sha256': sha256(target)}
    before['sidecar_backups'] = sidecars
    before['audio_sha256'] = sha256(job['audio_path'])
    before['audio_duration_seconds'] = probe_duration(job['audio_path'])
    if abs(before['audio_duration_seconds'] - entry['audio_duration_seconds']) > 1:
        raise ValueError('Source audio duration changed since preview')
    # Preserve other human artifacts and notes without copying audio.
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    before['related_rows'] = {}
    for table in ('notes', 'summaries', 'transcription_notes', 'transcription_summaries'):
        if table in tables:
            columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
            link = next((c for c in ('transcription_job_id','transcription_id','job_id') if c in columns), None)
            if link:
                before['related_rows'][table] = [dict(r) for r in conn.execute(f'SELECT * FROM {table} WHERE {link}=?', (job['id'],))]
    write_json(job_dir / 'snapshot.before.json', before)
    write_json(job_dir / 'speaker_mappings.before.json', before['mappings'])
    paths.update(snapshot_before=str(job_dir / 'snapshot.before.json'), speaker_mappings_backup=str(job_dir / 'speaker_mappings.before.json'))
    return before, paths


def assert_idle(conn, api_base):
    count = conn.execute("SELECT count(*) FROM transcription_jobs WHERE deleted_at IS NULL AND status IN ('pending','processing')").fetchone()[0]
    if count:
        raise RuntimeError(f'Plume has {count} active/queued jobs; no work submitted')
    health = local_request(api_base, '/health')
    if health.get('status') != 'healthy':
        raise RuntimeError('Plume API is not healthy')
    config = local_request(api_base, '/api/v1/llm/config')
    from urllib.parse import urlparse
    if config.get('provider') != 'ollama' or urlparse(config.get('base_url','')).hostname not in ('localhost','127.0.0.1','::1'):
        raise RuntimeError('Completion hooks must use local Ollama before private archive repair')


def compare_one(entry, run_dir, db, api_base=None):
    before = json.loads(Path(entry['backup']['snapshot_before']).read_text())
    with read_db(db) as conn:
        after = snapshot(conn, entry['job_id'])
    result = evaluate(before, after, entry.get('expected_speakers',0), entry['kind'], entry['max_speakers'])
    if after['job']['status'] == 'completed':
        with read_db(db) as conn:
            for table, rows in before.get('related_rows', {}).items():
                for original in rows:
                    current = conn.execute(f'SELECT * FROM {table} WHERE id=?', (original['id'],)).fetchone()
                    if current is None or any(current[k] != v for k,v in original.items() if k != 'updated_at'):
                        result['state'] = 'rejected'
                        result['metadata_verdict'] = 'changed'
                        result['reasons'].append('Human note/summary row changed or disappeared: ' + table)
        if before['job'].get('summary') and before['job']['summary'] != after['job'].get('summary'):
            result['state'] = 'rejected'
            result['metadata_verdict'] = 'changed'
            result['reasons'].append('Previous job summary changed or cleared')
    if after['job']['status'] != 'completed':
        return result
    audio = after['job'].get('audio_path') or ''
    if not Path(audio).is_file() or sha256(audio) != before['audio_sha256']:
        result['state'] = 'rejected'
        result['reasons'].append('Audio checksum changed or audio missing')
    md = Path(after['job'].get('transcript_markdown_path') or '')
    if not md.is_file() or md.stat().st_size == 0:
        result['state'] = 'rejected'
        result['reasons'].append('Markdown artifact missing or empty')
    if after['job'].get('model_family') != 'whisper' or after['job'].get('model') != 'large-v3' or after['job'].get('diarize_model') != 'nvidia_sortformer':
        result['state'] = 'rejected'
        result['reasons'].append('Unexpected transcription configuration')
    execution = after.get('execution') or {}
    if not execution or execution.get('id') == (before.get('execution') or {}).get('id') or execution.get('status') != 'completed':
        result['state'] = 'rejected'
        result['reasons'].append('No new completed execution proves this rerun finished')
    for key in ('model_family','model','device','compute_type','vad_method','vad_preprocess','condition_on_previous_text','diarize_model','max_speakers'):
        if execution.get('actual_' + key) != entry['params'].get(key):
            result['state'] = 'rejected'
            result['reasons'].append('Execution setting mismatch: ' + key)
    result['api_verification'] = 'not_performed'
    if api_base and after.get('metrics'):
        try:
            payload = local_request(api_base, '/api/v1/transcription/' + entry['job_id'] + '/transcript')
            if payload.get('job_id') != entry['job_id'] or not payload.get('available') or not payload.get('transcript', {}).get('segments'):
                raise ValueError('Missing or wrong transcript API response')
            returned = payload['transcript']
            words = load_script('build-archive-quality-audit').words_from(' '.join(s.get('text', '') for s in returned['segments']))
            query = next((w for w in words if len(w) >= 7 and w not in ('segments','speaker','speaker_name')), None)
            if not query:
                raise ValueError('No searchable transcript word')
            found = local_request(api_base, '/api/v1/transcription/list?' + urlencode({'q': query, 'limit': 2000, 'all_vaults': 'true'}))
            if entry['job_id'] not in {j['id'] for j in found.get('jobs', [])}:
                raise ValueError('Transcript not returned by Plume search')
            result['api_verification'] = 'passed'
        except Exception as e:
            result['reasons'].append('Plume open/search verification failed: ' + str(e))
            if result['state'] == 'completed_unreviewed':
                result['state'] = 'improved_review'
    if result['state'] == 'completed_unreviewed' and result['api_verification'] == 'passed':
        result['state'] = 'accepted'
        result['reasons'] = ['All automated text, speaker, metadata, file, and Plume API gates passed; semantic accuracy not certified']
    write_json(run_dir / entry['job_id'] / 'snapshot.after.json', after)
    write_json(run_dir / entry['job_id'] / 'comparison.json', result)
    return result


def apply_plan(args):
    with ExitStack() as stack:
        return _apply_plan(args, stack)


def _apply_plan(args, stack):
    plan = json.loads(args.apply_plan.read_text())
    if plan.get('schema_version') != 2 or plan['db'] != str(args.db.resolve()):
        raise ValueError('Plan schema/database mismatch')
    jobs = plan['jobs']
    if len({j['job_id'] for j in jobs}) != len(jobs):
        raise ValueError('Plan contains duplicate job IDs')
    if not jobs or sum(float(j.get('audio_duration_seconds') or j['duration_seconds']) for j in jobs) > 6 * 3600:
        raise ValueError('Apply requires a nonempty plan of at most six audio hours')
    for j in jobs:
        if j['block_reason']:
            raise ValueError(j['block_reason'])
        if j['kind'] == 'speakers' and (not 2 <= j['expected_speakers'] <= 4 or not j['speaker_evidence'].get('reason')):
            raise ValueError('Speaker repairs require reviewed evidence/count; title suffix alone is insufficient')
    args.runs_dir.mkdir(parents=True, exist_ok=True)
    lock = stack.enter_context((args.runs_dir / '.archive-repair.lock').open('a'))
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    old_lock = args.runs_dir / '.overnight-quality-reruns.pid'
    if old_lock.exists():
        try:
            os.kill(int(old_lock.read_text().strip()), 0)
        except ProcessLookupError:
            pass
        else:
            raise RuntimeError('Legacy controller is running')
    ledger = stack.enter_context(Ledger(args.ledger))
    run_dir = args.runs_dir / ('archive-repair-' + time.strftime('%Y%m%dT%H%M%S') + f'-{os.getpid()}')
    run_dir.mkdir(exist_ok=False)
    manifest = {'schema_version': 2, 'created_at': now(), 'db': str(args.db), 'plan': str(args.apply_plan), 'jobs': []}
    write_json(run_dir / 'plan.json', plan)
    write_json(run_dir / 'manifest.json', manifest)
    print('run_dir\t' + str(run_dir), flush=True)
    base_params = load_script('queue-whisperx-quality-rerun').WHISPERX_PARAMS
    with read_db(args.db) as conn:
        for entry in jobs:
            if ledger.conn.execute('SELECT 1 FROM jobs WHERE job_id=?', (entry['job_id'],)).fetchone() is None:
                raise ValueError('Job missing from progress ledger; regenerate preview')
            if ledger.state(entry['job_id']) in ('accepted','queued','improved_review','rejected'):
                raise RuntimeError('Ledger prevents repeat dispatch: ' + entry['job_id'])
            assert_idle(conn, args.api_base)
            if min(shutil.disk_usage(args.db.parent).free, shutil.disk_usage(run_dir).free) < 12 * 1024**3:
                raise RuntimeError('Free space below 12 GiB; no work submitted')
            before, paths = backup(conn, run_dir, entry)
            params = {**base_params, 'max_speakers': entry['max_speakers'], 'callback_url': ''}
            attempt = {**entry, 'backup': paths, 'params': params, 'queued': False, 'state': 'queued', 'dispatch_at': now()}
            manifest['jobs'].append(attempt)
            # Commit intent and backups BEFORE issuing a potentially uncertain POST.
            write_json(run_dir / 'manifest.json', manifest)
            ledger.record(run_dir, entry['job_id'], 'queued', {'reasons': ['Dispatch intent persisted; do not retry automatically'], 'backup': paths, 'params': params})
            try:
                response = local_request(args.api_base, '/api/v1/transcription/' + entry['job_id'] + '/start', params)
                if response.get('id') != entry['job_id']:
                    raise ValueError('Unexpected start response')
            except Exception as e:
                attempt['dispatch_error'] = str(e)
                write_json(run_dir / 'manifest.json', manifest)
                raise RuntimeError('Dispatch outcome uncertain; inspect DB/executions before retry') from e
            attempt['queued'] = True
            write_json(run_dir / 'manifest.json', manifest)
            print('queued\t' + entry['job_id'], flush=True)
            deadline = time.monotonic() + args.timeout_hours * 3600
            while True:
                row = conn.execute('SELECT status FROM transcription_jobs WHERE id=?', (entry['job_id'],)).fetchone()
                if row and row['status'] in ('completed','failed'):
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError('Job timeout; queued ledger state retained for recovery')
                time.sleep(min(args.poll_seconds, 30))
            # Completion hooks rewrite speaker labels after status=completed.
            # Wait for artifact/metadata to stabilize before hashing and comparing.
            stable, previous = 0, None
            settle_deadline = time.monotonic() + 15 * 60
            while stable < 60:
                current = snapshot(conn, entry['job_id'])
                fingerprint = (current['transcript_sha256'], current['job']['updated_at'], json.dumps(current['mappings']))
                stable = stable + 10 if fingerprint == previous else 0
                previous = fingerprint
                if time.monotonic() > settle_deadline:
                    raise RuntimeError('Postprocessing did not settle; stop for review')
                time.sleep(10)
            result = compare_one(attempt, run_dir, args.db, args.api_base)
            attempt['state'], attempt['comparison'] = result['state'], result
            write_json(run_dir / 'manifest.json', manifest)
            ledger.record(run_dir, entry['job_id'], result['state'], {**result, 'backup': paths, 'params': params})
            print('comparison\t' + entry['job_id'] + '\t' + result['state'] + '\t' + '; '.join(result['reasons']), flush=True)
            audit = load_script('build-archive-quality-audit')
            rows, summary = audit.scan(args.db)
            ledger.sync([vars(r) for r in rows])
            audit.write_csv(run_dir / 'audit-after.csv', rows)
            write_json(run_dir / 'audit-summary.json', summary)
            if result['state'] != 'accepted':
                print('stopped\tQuality gate requires review; remaining plan not queued', flush=True)
                return 2
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', type=Path, default=DEFAULT_DB)
    p.add_argument('--audit', type=Path, default=DATA / 'archive-quality-audit-latest.csv')
    p.add_argument('--ledger', type=Path, default=DEFAULT_LEDGER)
    p.add_argument('--runs-dir', type=Path, default=DATA / 'retranscription-runs')
    p.add_argument('--category', choices=['text','speakers','metadata','files','availability'], default='text')
    p.add_argument('--severity', nargs='+', default=['Critical'])
    p.add_argument('--model')
    p.add_argument('--diarizer')
    p.add_argument('--human', choices=['any','only','exclude'], default='any')
    p.add_argument('--prior-run')
    p.add_argument('--verdict')
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--max-hours', type=float, default=6)
    p.add_argument('--max-duration-hours', type=float, default=6)
    p.add_argument('--speaker-evidence', type=Path)
    p.add_argument('--verified-speakers-only', action='store_true')
    p.add_argument('--output', type=Path, default=DATA / 'archive-repair-preview.json')
    p.add_argument('--apply-plan', type=Path)
    p.add_argument('--api-base', default='http://127.0.0.1:59152')
    p.add_argument('--poll-seconds', type=int, default=15)
    p.add_argument('--timeout-hours', type=float, default=24)
    args = p.parse_args()
    if min(args.limit,args.max_hours,args.max_duration_hours,args.poll_seconds,args.timeout_hours) <= 0:
        p.error('Limits and intervals must be positive')
    if args.apply_plan:
        return apply_plan(args)
    build_plan(args)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
