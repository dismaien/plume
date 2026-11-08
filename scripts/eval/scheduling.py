"""Default idle-only scheduling; optional bounded GPU use beside one CPU ASR.

The explicit permit pins a completed diarization artifact and the still-running
CPU ASR process. Any queued/new work, expired permit or GPU diarizer revokes it.
No process is stopped here and no live database is ever written.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time


def execution_slot(database, device=None, permit_file=None):
    try:return _execution_slot(database,device,permit_file)
    except (OSError,ValueError,KeyError,sqlite3.Error,subprocess.SubprocessError):return False


def _execution_slot(database, device=None, permit_file=None):
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True) as db:
        count=db.execute("select count(*) from transcription_jobs where status in ('pending','processing')").fetchone()[0]
        if not count:return True
        if device!='mps' or not permit_file:return False
        permit=json.loads(Path(permit_file).read_text())
        if (Path(permit['database']).resolve()!=Path(database).resolve()
                or time.time()>=permit['expires_at_unix']):return False
        active=db.execute("select id,status from transcription_jobs where status in ('pending','processing')").fetchall()
        if active!=[(permit['job_id'],'processing')]:return False
        execution=db.execute('select actual_device from transcription_job_executions where transcription_job_id=? order by created_at desc limit 1',
                             (permit['job_id'],)).fetchone()
        if not execution or execution[0]!='cpu':return False
    artifact=Path(permit['completed_diarization'])
    if hashlib.sha256(artifact.read_bytes()).hexdigest()!=permit['completed_diarization_sha256']:return False
    output=json.loads(artifact.read_text())
    if output.get('model')!='pyannote/speaker-diarization-community-1':return False
    command=subprocess.run(['ps','-p',str(permit['asr_pid']),'-o','command='],capture_output=True,text=True)
    if (command.returncode or 'whisper_full_audio.py' not in command.stdout
            or permit['job_id'] not in command.stdout or '--device cpu' not in command.stdout):return False
    commands=subprocess.check_output(['ps','-axo','command='],text=True).splitlines()
    if any('pyannote_diarize.py' in command for command in commands):return False
    return True
