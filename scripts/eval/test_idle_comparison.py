import importlib.util
import json
from pathlib import Path
import sqlite3
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
