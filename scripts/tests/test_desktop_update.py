import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

spec=importlib.util.spec_from_file_location('desktop_update',Path(__file__).resolve().parents[1]/'install-desktop-update.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def setup(tmp_path,monkeypatch):
    old=tmp_path/'Plume.app';stage=tmp_path/'stage/Plume.app'
    for app,content in [(old,b'old build'),(stage,b'new build')]:
        binary=app/'Contents/Resources/backend/quill';binary.parent.mkdir(parents=True);binary.write_bytes(content)
    database=tmp_path/'archive.db'
    with sqlite3.connect(database) as db:
        db.execute('create table transcription_jobs(id text,status text,transcript text)')
        db.execute('insert into transcription_jobs values(?,?,?)',('accepted','completed','human correction'))
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps(module.app_manifest(stage)))
    ready=tmp_path/'ready.json';ready.write_text(json.dumps(dict(status='ready',installed_app=str(old),staged_app=str(stage),
        database=str(database),app_manifest=str(manifest),app_manifest_sha256=module.digest(manifest),
        backend_sha256=module.digest(stage/'Contents/Resources/backend/quill'),source_commit='test-commit',install_receipt=str(tmp_path/'receipt.json'))))
    monkeypatch.setattr(module,'assert_backend_stopped',lambda app:None)
    def verify(app,expected):
        if module.app_manifest(app)!=expected:raise RuntimeError('App contents changed')
    monkeypatch.setattr(module,'verify_app',verify)
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='Commit: test-commit\n',returncode=0))
    return ready,old,stage,database


def test_active_queue_never_moves_the_app(tmp_path,monkeypatch):
    ready,old,stage,database=setup(tmp_path,monkeypatch)
    with sqlite3.connect(database) as db:db.execute("update transcription_jobs set status='processing'")
    with pytest.raises(RuntimeError,match='pending/processing'):module.install(ready)
    assert (old/'Contents/Resources/backend/quill').read_bytes()==b'old build' and stage.exists()
    assert not list(tmp_path.glob('install-*'))


def test_changed_staged_binary_is_rejected_before_backup_or_swap(tmp_path,monkeypatch):
    ready,old,stage,_=setup(tmp_path,monkeypatch)
    (stage/'Contents/Resources/backend/quill').write_bytes(b'changed after verification')
    with pytest.raises(RuntimeError,match='App contents changed'):module.install(ready)
    assert (old/'Contents/Resources/backend/quill').read_bytes()==b'old build'
    assert not list(tmp_path.glob('install-*'))


def test_swap_failure_restores_app_and_retains_database_backup(tmp_path,monkeypatch):
    ready,old,stage,database=setup(tmp_path,monkeypatch)
    replace=module.os.replace
    def fail_stage(source,destination):
        if source==stage:raise OSError('simulated move failure')
        return replace(source,destination)
    monkeypatch.setattr(module.os,'replace',fail_stage)
    with pytest.raises(OSError,match='simulated'):module.install(ready)
    assert (old/'Contents/Resources/backend/quill').read_bytes()==b'old build'
    assert len(list(tmp_path.glob('install-*/quill-before-install.db')))==1
    with sqlite3.connect(database) as db:assert db.execute('select transcript from transcription_jobs').fetchone()[0]=='human correction'


def test_successful_file_swap_still_requires_launch_verification(tmp_path,monkeypatch):
    ready,old,_,database=setup(tmp_path,monkeypatch)
    before=module.database_fingerprints(database)
    receipt=module.install(ready)
    assert receipt['status']=='installed_awaiting_launch_verification'
    assert module.database_fingerprints(database)==before
    assert (old/'Contents/Resources/backend/quill').read_bytes()==b'new build'
    assert (Path(receipt['backup'])/'Plume.app/Contents/Resources/backend/quill').read_bytes()==b'old build'
