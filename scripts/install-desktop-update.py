#!/usr/bin/env python3
"""Guarded filesystem-only install of an already verified Plume app.

The calling agent must quit/relaunch through CUA. This script performs no UI
actions, generation, transcription, migrations, database restores or downloads.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import uuid


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def app_manifest(app):
    result = {}
    for path in sorted(Path(app).rglob('*')):
        if path.name == '.DS_Store':
            continue
        relative = str(path.relative_to(app))
        if path.is_symlink():
            result[relative] = {'link': os.readlink(path)}
        elif path.is_file():
            result[relative] = {'sha256': digest(path), 'mode': path.stat().st_mode & 0o777}
    return result


def verify_app(app, expected):
    if app_manifest(app) != expected:
        raise RuntimeError('App contents or executable permissions differ from the verified manifest')
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True, capture_output=True)


def connect_ro(database):
    return sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)


def assert_idle(database):
    with connect_ro(database) as db:
        active = db.execute("SELECT count(*) FROM transcription_jobs WHERE status IN ('pending','processing')").fetchone()[0]
    if active:
        raise RuntimeError(f'Plume still has {active} pending/processing jobs; leave the installed app untouched')


def assert_backend_stopped(app):
    for executable in ('Contents/Resources/backend/quill', 'Contents/MacOS/Plume'):
        pattern = '^' + re.escape(str(Path(app) / executable)) + r'(\s|$)'
        process = subprocess.run(['pgrep', '-f', pattern], capture_output=True, text=True)
        if process.returncode == 0:
            raise RuntimeError('Plume is still running; quit it through CUA before installing')
        if process.returncode != 1:
            raise RuntimeError('Could not verify that Plume exited')


def database_fingerprints(database):
    """Hash saved content without exposing it or depending on recreated row IDs."""
    results = {}
    with connect_ro(database) as db:
        db.text_factory = bytes
        tables = {row[0].decode() for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('transcription_jobs', 'speaker_mappings', 'transcript_revisions', 'summaries', 'notes', 'contacts', 'summary_templates'):
            if table not in tables:
                continue
            ignored = {'updated_at'}
            if table in ('speaker_mappings', 'summaries'):
                ignored |= {'id', 'created_at'}
            columns = [row[1].decode() for row in db.execute(f'PRAGMA table_info("{table}")') if row[1].decode() not in ignored]
            names = ','.join('"'+name.replace('"', '""')+'"' for name in columns)
            hashes = []
            for row in db.execute(f'SELECT {names} FROM "{table}"'):
                hasher = hashlib.sha256()
                for value in row:
                    blob = b'N' if value is None else b'B'+value if isinstance(value, bytes) else (type(value).__name__+':'+str(value)).encode()
                    hasher.update(len(blob).to_bytes(8, 'big')); hasher.update(blob)
                hashes.append(hasher.hexdigest())
            results[table] = {'rows': len(hashes), 'sha256': hashlib.sha256('\n'.join(sorted(hashes)).encode()).hexdigest()}
    return results


def _install(ready_path):
    ready_path = Path(ready_path).resolve()
    ready = json.loads(ready_path.read_text())
    if ready.get('status') != 'ready':
        raise RuntimeError('The integration release has not been verified and marked ready')
    app, stage = Path(ready['installed_app']).resolve(), Path(ready['staged_app']).resolve()
    database = Path(ready['database'])
    if app == stage or app.suffix != '.app' or stage.suffix != '.app':
        raise ValueError('Expected distinct installed and staged app bundles')
    assert_idle(database)
    assert_backend_stopped(app)
    manifest_path = Path(ready['app_manifest'])
    if digest(manifest_path) != ready['app_manifest_sha256']:
        raise RuntimeError('Verified app manifest changed')
    expected = json.loads(manifest_path.read_text())
    verify_app(stage, expected)
    backend = stage / 'Contents/Resources/backend/quill'
    if digest(backend) != ready['backend_sha256']:
        raise RuntimeError('Staged backend differs from the tested build')
    version = subprocess.run([str(backend), '--version'], capture_output=True, text=True, check=True).stdout
    if 'Commit: '+ready['source_commit']+'\n' not in version:
        raise RuntimeError('Staged app was not built from the verified commit')

    backup = ready_path.parent / ('install-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    backup.mkdir(mode=0o700)
    before = database_fingerprints(database)
    with connect_ro(database) as source, sqlite3.connect(backup / 'quill-before-install.db') as destination:
        source.backup(destination)
    (backup / 'before-fingerprints.json').write_text(json.dumps(before, indent=2))
    assert_idle(database)
    assert_backend_stopped(app)
    previous = backup / 'Plume.app'
    os.replace(app, previous)
    try:
        os.replace(stage, app)
        verify_app(app, expected)
    except BaseException:
        if app.exists():
            os.replace(app, backup / 'rejected-Plume.app')
        os.replace(previous, app)
        raise
    result = dict(status='installed_awaiting_launch_verification', source_commit=ready['source_commit'],
                  installed_app=str(app), backup=str(backup), backend_sha256=digest(app / 'Contents/Resources/backend/quill'),
                  before_fingerprints=before, database_restored=False,
                  next='Launch via CUA, verify health/Review UI and compare current database content with before_fingerprints. Never restore a live DB automatically.')
    output = Path(ready['install_receipt'])
    output.write_text(json.dumps(result, indent=2)); output.chmod(0o600)
    return result


def install(ready_path):
    # A single guarded handoff owns the swap even if a monitor is retried.
    with (Path(ready_path).resolve().parent / 'install.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Another Plume installation is already in progress') from error
        return _install(ready_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ready', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(install(args.ready), indent=2))
