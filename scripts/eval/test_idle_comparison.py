import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time
import pytest


def load(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_nemotron_output_preserves_brief_overlap_without_fake_confidence():
    module=load('stage_diar_nemotron3')
    result=module.canonical_segments([{'Start':0,'End':4,'Speaker':0},{'Start':1,'End':1.02,'Speaker':1}],4)
    assert len(result)==2 and result[1]['end']==1.02
    assert 'confidence' not in result[1] and result[1]['speaker']=='speaker_1'


def test_active_queue_blocks_model_load_and_comparison(tmp_path):
    database=tmp_path/'live.db'
    with sqlite3.connect(database) as db:
        db.execute('create table transcription_jobs(status text)');db.execute("insert into transcription_jobs values('processing')")
    module=load('stage_diar_nemotron3')
    with pytest.raises(RuntimeError,match='queue is active'):module.check_idle(database)
    plan=tmp_path/'plan.json';plan.write_text(json.dumps({'database':str(database)}))
    result=load('run_idle_comparison').run(plan)
    assert result['status']=='waiting_for_foreground_queue_or_disk'
    assert not (tmp_path/'comparison-state.json').exists()


def test_model_comparison_waits_for_the_exact_verified_install(tmp_path):
    module=load('run_idle_comparison')
    receipt=tmp_path/'installed.json'
    plan={'requires_verified_install':{'receipt':str(receipt),'source_commit':'new','backend_sha256':'expected'}}
    assert not module.installation_verified(plan)
    for status,commit,sha,expected in (
        ('installed_awaiting_launch_verification','new','expected',False),
        ('verified','old','expected',False),
        ('verified','new','wrong',False),
        ('verified','new','expected',True),
    ):
        receipt.write_text(json.dumps({'status':status,'source_commit':commit,'backend_sha256':sha}))
        assert module.installation_verified(plan)==expected


def test_new_foreground_job_stops_only_the_owned_comparison_process(tmp_path,monkeypatch):
    module=load('run_idle_comparison')
    database=tmp_path/'live.db'
    with sqlite3.connect(database) as db:db.execute('create table transcription_jobs(status text)')
    stage=tmp_path/'stage.py';stage.write_text('import time\ntime.sleep(60)\n')
    audio=tmp_path/'audio';audio.write_bytes(b'synthetic runner test, not audio')
    reference=tmp_path/'reference.json';reference.write_text('{}')
    state_path=tmp_path/'comparison-state.json'
    plan=tmp_path/'plan.json'
    plan.write_text(json.dumps({'database':str(database),'python':sys.executable,'model':'unused',
        'stage':str(stage),'stage_sha256':module.digest(stage),'timeout_seconds':10,
        'experiments':[{'name':'preemption-test','audio':str(audio),'audio_sha256':module.digest(audio),
            'reference':str(reference),'reference_sha256':module.digest(reference),'device':'cpu'}]}))
    class Disk:free=20*1024**3
    monkeypatch.setattr(module.shutil,'disk_usage',lambda _:Disk())
    triggered=threading.Event()
    def enqueue():
        until=time.monotonic()+5
        while time.monotonic()<until:
            if state_path.exists():
                with sqlite3.connect(database) as db:db.execute("insert into transcription_jobs values('processing')")
                triggered.set();return
            time.sleep(.01)
    thread=threading.Thread(target=enqueue);thread.start()
    try:result=module.run(plan)
    finally:thread.join(timeout=6)
    assert triggered.is_set()
    assert result['status']=='paused_for_foreground_queue_or_disk'
    assert result['accepted'] is False and result['exit_code'] is not None
    assert result['seconds']<8
    assert json.loads((Path(result['attempt'])/'receipt.json').read_text())['status']==result['status']
