import hashlib
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

import scheduling


@pytest.fixture
def permitted(tmp_path,monkeypatch):
    database=tmp_path/'live.db'
    with sqlite3.connect(database) as db:
        db.execute('create table transcription_jobs(id text,status text)')
        db.execute("insert into transcription_jobs values('final-job','processing')")
        db.execute('create table transcription_job_executions(transcription_job_id text,actual_device text,created_at text)')
        db.execute("insert into transcription_job_executions values('final-job','cpu','now')")
    artifact=tmp_path/'diarization.json'
    artifact.write_text(json.dumps({'model':'pyannote/speaker-diarization-community-1'}))
    path=tmp_path/'permit.json'
    permit=dict(database=str(database),job_id='final-job',asr_pid=123,
                completed_diarization=str(artifact),completed_diarization_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                expires_at_unix=time.time()+30)
    path.write_text(json.dumps(permit))
    monkeypatch.setattr(scheduling.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='python whisper_full_audio.py final-job --device cpu'))
    monkeypatch.setattr(scheduling.subprocess,'check_output',lambda *a,**k:'python whisper_full_audio.py final-job --device cpu')
    return database,path,permit,artifact


def test_only_explicit_gpu_permit_can_share_the_final_cpu_asr(permitted):
    database,path,_,_=permitted
    assert not scheduling.execution_slot(database)
    assert not scheduling.execution_slot(database,'cpu',path)
    assert scheduling.execution_slot(database,'mps',path)


def test_new_foreground_work_revokes_gpu_slot(permitted):
    database,path,_,_=permitted
    with sqlite3.connect(database) as db:db.execute("insert into transcription_jobs values('new-job','pending')")
    assert not scheduling.execution_slot(database,'mps',path)


def test_a_running_gpu_diarizer_revokes_slot(permitted,monkeypatch):
    database,path,_,_=permitted
    monkeypatch.setattr(scheduling.subprocess,'check_output',lambda *a,**k:'python pyannote_diarize.py')
    assert not scheduling.execution_slot(database,'mps',path)


def test_expired_or_changed_proof_is_rejected(permitted):
    database,path,permit,artifact=permitted
    permit['expires_at_unix']=time.time()-1;path.write_text(json.dumps(permit))
    assert not scheduling.execution_slot(database,'mps',path)
    permit['expires_at_unix']=time.time()+30;path.write_text(json.dumps(permit))
    artifact.write_text('{}')
    assert not scheduling.execution_slot(database,'mps',path)
