"""Shared local archive repair ledger, snapshots, and conservative acceptance gates.

The canonical audit supplies classification and text/speaker metrics. A low score
alone is never acceptance. Private state belongs beside the archive, outside Git.
"""
from __future__ import annotations

import csv
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request

SCRIPT_DIR = Path(__file__).resolve().parent
DATA = Path.home() / "Documents/Voice Notes Archive/20-quill-processing"
DEFAULT_DB = Path.home() / "Library/Application Support/plume-desktop/data/quill.db"
DEFAULT_LEDGER = DATA / "archive-repair-ledger.sqlite3"


def load_script(name):
    key = name.replace('-', '_')
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, SCRIPT_DIR / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def read_db(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def trusted(mapping):
    return bool(mapping.get('custom_name')) and (
        mapping.get('match_source') == 'manual'
        or mapping.get('review_status') in ('accepted', 'corrected')
        or (not mapping.get('match_source') and mapping['custom_name'] != mapping.get('original_speaker'))
    )


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
          CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY, state TEXT NOT NULL, reason TEXT NOT NULL,
            audit_json TEXT NOT NULL, updated_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS attempts (
            run_dir TEXT NOT NULL, job_id TEXT NOT NULL, state TEXT NOT NULL,
            payload_json TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(run_dir, job_id));
        ''')
        self.conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.conn.close()

    def sync(self, rows):
        for r in rows:
            state, reason = 'not_attempted', 'Awaiting repair selection or human review'
            if r.get('record_type') == 'reference_document':
                state, reason = 'blocked_reference_document', 'Granola text reference; no source audio attached, ASR not applicable'
            elif not r.get('audio_exists') or str(r['audio_exists']).lower() == 'false':
                state, reason = 'blocked_missing_audio', 'Reconcile recorded audio path with preservation archive'
            elif r['status'] != 'completed':
                state, reason = 'failed' if r['status'] == 'failed' else 'not_attempted', 'Availability queue: inspect error/source before retry'
            elif int(r.get('human_speaker_mappings', 0)):
                state, reason = 'blocked_metadata_reconciliation', 'Protected mappings require reviewed label reconciliation before in-place rerun'
            self.conn.execute('''INSERT INTO jobs VALUES (?,?,?,?,?)
              ON CONFLICT(job_id) DO UPDATE SET audit_json=excluded.audit_json,
              state=CASE WHEN jobs.state IN ('not_attempted','blocked_missing_audio','blocked_metadata_reconciliation','blocked_reference_document','failed') THEN excluded.state ELSE jobs.state END,
              reason=CASE WHEN jobs.state IN ('not_attempted','blocked_missing_audio','blocked_metadata_reconciliation','blocked_reference_document','failed') THEN excluded.reason ELSE jobs.reason END''',
              (r['job_id'], state, reason, json.dumps(r), now()))
        self.conn.commit()

    def import_legacy(self, runs_dir):
        # Narrow historical verdicts are recorded as history, never upgraded to accepted.
        for path in sorted(Path(runs_dir).glob('*/manifest.json')):
            manifest = json.loads(path.read_text())
            if manifest.get('schema_version') == 2:
                continue
            for job in manifest.get('jobs', []):
                if not job.get('queued') or not job.get('job_id'):
                    continue
                payload = {'legacy': True, 'reason': 'Legacy gate did not verify full metrics/metadata',
                           'created_at': manifest.get('created_at'), 'model': job.get('model'),
                           'backup': job.get('backup'), 'params': job.get('params') or manifest.get('params')}
                self.conn.execute('INSERT OR IGNORE INTO attempts VALUES (?,?,?,?,?)',
                                  (str(path.parent), job['job_id'], 'completed_unreviewed', json.dumps(payload), now()))
        self.conn.execute("UPDATE jobs SET state='completed_unreviewed',reason='Historical repair exists; full acceptance not established' WHERE state='not_attempted' AND json_extract(audit_json, '$.status')='completed' AND EXISTS (SELECT 1 FROM attempts WHERE attempts.job_id=jobs.job_id)")
        self.conn.commit()

    def history(self, job_id):
        return [dict(r) for r in self.conn.execute(
            'SELECT run_dir,state,payload_json,updated_at FROM attempts WHERE job_id=? ORDER BY updated_at,run_dir', (job_id,))]

    def state(self, job_id):
        r = self.conn.execute('SELECT state FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        return r['state'] if r else 'not_attempted'

    def record(self, run_dir, job_id, state, payload):
        self.conn.execute('INSERT INTO attempts VALUES (?,?,?,?,?) ON CONFLICT(run_dir,job_id) DO UPDATE SET state=excluded.state,payload_json=excluded.payload_json,updated_at=excluded.updated_at',
                          (str(run_dir), job_id, state, json.dumps(payload), now()))
        self.conn.execute('UPDATE jobs SET state=?,reason=?,updated_at=? WHERE job_id=?',
                          (state, '; '.join(payload.get('reasons', [])), now(), job_id))
        self.conn.commit()


def audit_rows(csv_path):
    with Path(csv_path).open(encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    if not rows or len({r['job_id'] for r in rows}) != len(rows):
        raise ValueError('Audit must contain unique parent jobs')
    return rows


PROTECTED_FIELDS = ('title', 'recorded_at', 'original_filename', 'folder', 'vault_id',
                    'created_at', 'is_source_part', 'source_parent_id', 'source_group_id',
                    'source_part_index', 'source_part_count', 'file_hash')
SECRET_FIELDS = {'hf_token', 'api_key'}


def snapshot(conn, job_id):
    audit = load_script('build-archive-quality-audit')
    row = conn.execute('SELECT * FROM transcription_jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        raise ValueError('Job missing: ' + job_id)
    job = {k: v for k, v in dict(row).items() if k not in SECRET_FIELDS}
    mappings = [dict(r) for r in conn.execute('SELECT * FROM speaker_mappings WHERE transcription_job_id=? ORDER BY original_speaker', (job_id,))]
    metrics, error = None, None
    path = job.get('transcript_json_path')
    try:
        metrics = audit.parse_transcript(Path(path or ''))
    except (OSError, ValueError, TypeError, AttributeError) as e:
        error = str(e)
    known = audit.title_people(job.get('title') or '') | {m['custom_name'] for m in mappings if trusted(m)}
    title_duration = audit.parse_title_duration(job.get('title') or '')
    delta = abs(title_duration - metrics['duration']) if metrics and title_duration else None
    audio_exists = audit.safe_size(job.get('audio_path') or '')[0]
    classification = audit.classify(job, metrics, known, audio_exists, metrics is not None, delta)
    execution_row = conn.execute('SELECT * FROM transcription_job_executions WHERE transcription_job_id=? ORDER BY created_at DESC, id DESC LIMIT 1', (job_id,)).fetchone()
    execution = {k:v for k,v in dict(execution_row).items() if k not in ('actual_hf_token','actual_api_key')} if execution_row else None
    return {'job': job, 'execution': execution, 'mappings': mappings, 'metrics': metrics, 'parse_error': error,
            'known_people': sorted(known), 'classification': classification,
            'transcript_sha256': sha256(path) if path and Path(path).is_file() else None}


def repetition_burden(metrics):
    # Uncapped: saturated audit text_score=100 cannot quantify improvement.
    return metrics['legacy_score'] + metrics['phrase_runs'] * 5 + max(0, metrics['max_repeated_tokens'] - 6) * 2 + metrics['suspicious_thanks'] * 4


def evaluate(before, after, expected_speakers=0, kind='text', max_speakers=4):
    reasons, review = [], []
    result = {'text_verdict': 'not_scored', 'diarization_verdict': 'not_scored',
              'metadata_verdict': 'not_scored', 'manual_spot_check': 'not_performed'}
    status = after['job']['status']
    if status != 'completed':
        return {**result, 'state': 'failed' if status == 'failed' else 'queued', 'reasons': ['Job status: ' + status]}
    b, a = before.get('metrics'), after.get('metrics')
    if not b or not a or not a['word_count']:
        return {**result, 'state': 'rejected', 'reasons': ['Missing, invalid, or empty before/after transcript']}
    for key in PROTECTED_FIELDS:
        old_value, new_value = before['job'].get(key), after['job'].get(key)
        if key in ('source_parent_id', 'source_group_id'):
            old_value, new_value = old_value or None, new_value or None
        if old_value != new_value:
            reasons.append('Protected metadata changed: ' + key)
    old_names = {(m['original_speaker'], m['custom_name'], m.get('contact_id')) for m in before['mappings'] if trusted(m)}
    new_names = {(m['original_speaker'], m['custom_name'], m.get('contact_id')) for m in after['mappings'] if trusted(m)}
    if old_names and old_names != new_names:
        reasons.append('Trusted speaker mappings require reconciliation')
    # Even identical labels do not establish identity across a new diarization.
    if old_names:
        review.append('Trusted speaker identities need time-aligned/manual reconciliation')
    result['metadata_verdict'] = 'preserved' if not reasons and not old_names else 'review_required'
    if a.get('invalid_timing_count'):
        reasons.append('Invalid segment timing in replacement')
    bs, ns = repetition_burden(b), repetition_burden(a)
    if a['legacy_score'] >= 250 or a['max_repeated_tokens'] >= 80:
        reasons.append('Catastrophic repetition remains')
    if ns > bs + max(10, bs * 0.1):
        reasons.append('Repetition/hallucination burden increased')
    if a['max_repeated_tokens'] >= 24 or a['legacy_score'] >= 50 or a['suspicious_thanks']:
        review.append('Residual repetition or implausibly timed filler needs audio review')
    if kind == 'text' and ns > bs * 0.8:
        review.append('Text improvement is less than 20%')
    result['text_verdict'] = 'improved' if ns < bs else 'stable' if ns == bs else 'worse'
    if a['word_count'] < b['word_count'] * 0.7:
        review.append('Word count fell by more than 30%; verify audio before accepting')
    if a['duration'] < b['duration'] * 0.85:
        review.append('Transcript endpoint fell by more than 15%')
    if a.get('speech_seconds', 0) < b.get('speech_seconds', 0) * 0.7:
        review.append('Speech coverage fell by more than 30%')
    if a.get('unlabeled_ratio', 0) > max(0.2, b.get('unlabeled_ratio', 0) + 0.1):
        review.append('Unlabeled speech increased')
    expected = max(expected_speakers, 2 if kind == 'speakers' else 0)
    if expected >= 2:
        if a['speaker_count'] < expected or a['dominant_ratio'] >= 0.90:
            review.append('Expected speakers remain missing or dominated')
        if kind == 'speakers' and not (a['dominant_ratio'] < b['dominant_ratio'] - 0.05 or a['speaker_count'] > b['speaker_count']):
            review.append('Diarization did not materially improve')
        result['diarization_verdict'] = 'recovered' if a['speaker_count'] >= expected and a['dominant_ratio'] < .9 else 'review_required'
    if a['speaker_count'] > max_speakers or a['switches_per_hour'] > 500:
        review.append('Possible diarization over-segmentation')
    if b['speaker_count'] >= 2 and a['speaker_count'] < 2:
        review.append('Previously multiple detected speakers now collapse to one')
    state = 'rejected' if reasons else 'improved_review' if review else 'completed_unreviewed'
    return {**result, 'state': state, 'reasons': reasons + review,
            'before_burden': bs, 'after_burden': ns,
            'before_classification': before['classification'], 'after_classification': after['classification'],
            'before_metrics': b, 'after_metrics': a}


def local_request(api_base, path, body=None):
    parsed = urlparse(api_base)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Archive API must use local loopback HTTP')
    req = Request(api_base.rstrip('/') + path, data=json.dumps(body).encode() if body is not None else None,
                  headers={'Content-Type': 'application/json'})
    # Never follow a redirect that could send private content elsewhere.
    import urllib.request
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=30) as response:
        return json.load(response)
