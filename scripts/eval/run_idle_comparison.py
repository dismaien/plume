#!/usr/bin/env python3
"""Execute a bounded, hash-pinned local comparison after foreground work.

Every attempt is private and immutable. New Plume work preempts this process;
completed and failed candidates are never silently rerun or accepted.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import time

from score_reference import digest, score
from scheduling import execution_slot


def idle(database):
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True) as db:
        return db.execute("select count(*) from transcription_jobs where status in ('pending','processing')").fetchone()[0]==0


def slot_available(plan):
    if not plan.get('cpu_asr_permit'):return idle(plan['database'])
    if any(case['device']!='mps' for case in plan['experiments']):return False
    return execution_slot(plan['database'],'mps',plan['cpu_asr_permit'])


def stop_owned_process(process):
    try:os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.wait(timeout=10)


def installation_verified(plan):
    requirement=plan.get('requires_verified_install')
    if not requirement:return True
    path=Path(requirement['receipt'])
    if not path.exists():return False
    receipt=json.loads(path.read_text())
    return (receipt.get('status')=='verified'
            and receipt.get('source_commit')==requirement['source_commit']
            and receipt.get('backend_sha256')==requirement['backend_sha256'])


def save_state(path,state):
    temporary=path.with_suffix('.writing.json')
    temporary.write_text(json.dumps(state,indent=2));os.replace(temporary,path)


def run(plan_path):
    plan_path=plan_path.resolve();plan=json.loads(plan_path.read_text());root=plan_path.parent
    if shutil.disk_usage(root).free < 12*1024**3 or not slot_available(plan):
        return {'status':'waiting_for_foreground_queue_or_disk'}
    if not installation_verified(plan):
        return {'status':'waiting_for_verified_installation'}
    with (root/'comparison.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'another_comparison_running'}
        state_path=root/'comparison-state.json'
        state=json.loads(state_path.read_text()) if state_path.exists() else {'results':{}}
        for experiment in plan['experiments']:
            name=experiment['name']
            previous=state['results'].get(name,{})
            if previous.get('status') in ('scored','failed'):
                continue
            if previous.get('status')=='running':
                # An interrupted parent must not create a second model process.
                try:os.kill(previous['pid'],0)
                except ProcessLookupError:
                    previous.update(status='failed',error='Comparison parent interrupted; inspect retained attempt before an explicit retry')
                    save_state(state_path,state)
                else:return {'status':'previous_comparison_process_still_running','pid':previous['pid']}
                return previous
            if not slot_available(plan):return {'status':'waiting_for_foreground_queue'}
            if not installation_verified(plan):return {'status':'waiting_for_verified_installation'}
            if digest(experiment['audio'])!=experiment['audio_sha256']:
                raise ValueError('Comparison source changed')
            reference=Path(experiment['reference'])
            if digest(reference)!=experiment['reference_sha256']:
                raise ValueError('Comparison reference changed')
            stage=Path(plan['stage'])
            if digest(stage)!=plan['stage_sha256']:
                raise ValueError('Comparison implementation changed after approval')
            attempt=root/name/f'attempt-{time.time_ns()}';attempt.mkdir(parents=True,mode=0o700)
            candidate=attempt/'result.json'
            command=['nice','-n','10',plan['python'],str(stage),'--audio',experiment['audio'],
                     '--out',str(candidate),'--model',plan['model'],'--db',plan['database'],'--device',experiment['device']]
            env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',
                     PYANNOTE_METRICS_ENABLED='0',PYTORCH_ENABLE_MPS_FALLBACK='0',OMP_NUM_THREADS='4')
            for key in list(env):
                if key in ('HF_TOKEN','HUGGING_FACE_HUB_TOKEN') or key.endswith('API_KEY'):env.pop(key)
            if plan.get('cpu_asr_permit'):
                env.update(PLUME_EVAL_CPU_ASR_PERMIT=plan['cpu_asr_permit'],OMP_NUM_THREADS='1',
                           OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
            else:env.pop('PLUME_EVAL_CPU_ASR_PERMIT',None)
            if plan.get('include_overlap_embeddings'):
                env['PLUME_EVAL_INCLUDE_OVERLAP_EMBEDDINGS']='1'
            else:env.pop('PLUME_EVAL_INCLUDE_OVERLAP_EMBEDDINGS',None)
            start=time.monotonic();status='running'
            with (attempt/'process.log').open('w') as log:
                process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
                state['results'][name]={'status':'running','pid':process.pid,'attempt':str(attempt),'experiment':experiment}
                save_state(state_path,state)
                while process.poll() is None:
                    if not slot_available(plan) or shutil.disk_usage(root).free < 12*1024**3:
                        status='paused_for_foreground_queue_or_disk';stop_owned_process(process);break
                    if time.monotonic()-start>plan.get('timeout_seconds',900):
                        status='failed';stop_owned_process(process);break
                    time.sleep(2)
            receipt={'status':status,'exit_code':process.returncode,'seconds':time.monotonic()-start,
                     'attempt':str(attempt),'experiment':experiment,'command':command,'accepted':False,
                     'plan_sha256':digest(plan_path)}
            if status=='running' and process.returncode==0 and candidate.exists():
                try:
                    result=score(reference,candidate)
                    (attempt/'score.json').write_text(json.dumps(result,indent=2,allow_nan=False))
                    receipt.update(status='scored',diarization=result['diarization'],candidate_sha256=digest(candidate))
                except Exception as error:receipt.update(status='failed',error=str(error))
            elif status=='running':receipt['status']='failed'
            (attempt/'receipt.json').write_text(json.dumps(receipt,indent=2))
            state['results'][name]=receipt
            save_state(state_path,state)
            print(name,receipt['status'],flush=True)
            if receipt['status']!='scored':return receipt
        return {'status':'complete','state':str(state_path),'accepted':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--plan',type=Path,required=True)
    print(json.dumps(run(parser.parse_args().plan),indent=2))
