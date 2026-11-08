#!/usr/bin/env python3
"""Run a declared local candidate matrix with isolated outputs and provenance.

Matrix: {audio, source_sha256, candidates:[{name,command:[...],output,engine,
engine_version,config}], reference?}. Use {audio}, {output}, {run_dir} placeholders.
Commands are local CLI adapters; no shell interpolation or live DB/API writes.
A run without a reviewed reference produces diagnostics, never accuracy rankings.
"""
import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from score_reference import digest, score


def run(matrix_path,out):
    matrix_path=matrix_path.resolve();matrix=json.loads(matrix_path.read_text());root=matrix_path.parent
    audio=(root/matrix['audio']).resolve()
    if digest(audio)!=matrix['source_sha256']:raise ValueError('Audio hash mismatch')
    out.mkdir(parents=True,exist_ok=False)
    names=set();results=[]
    for candidate in matrix['candidates']:
        name=candidate['name']
        if not name or name in names or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in name):
            raise ValueError('Candidate names must be unique safe directory names')
        names.add(name);directory=out/name;directory.mkdir()
        output=directory/Path(candidate.get('output','result.json')).name
        substitutions={'audio':str(audio),'output':str(output.resolve()),'run_dir':str(directory.resolve())}
        command=[arg.format(**substitutions) for arg in candidate['command']]
        if not all(candidate.get(k) for k in ('engine','engine_version','config')):
            raise ValueError('Each candidate needs engine, engine_version and config provenance')
        started=time.monotonic()
        env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYANNOTE_METRICS_ENABLED='0')
        with (directory/'process.log').open('w') as log:
            result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,env=env,cwd=root)
        receipt={'name':name,'candidate':candidate,'exit_code':result.returncode,'seconds':time.monotonic()-started,
                 'audio_sha256':matrix['source_sha256'],'command':command,'accuracy_status':'not_scored'}
        if result.returncode==0 and output.exists():
            receipt['output_sha256']=digest(output)
            score_path=output
            if matrix.get('transcript'):
                if not matrix.get('assembler'):
                    raise ValueError('Frozen-transcript comparison requires the production transcription-eval binary')
                frozen=(root/matrix['transcript']).resolve()
                receipt['frozen_transcript_sha256']=digest(frozen)
                assembled=directory/'assembled.json'
                with (directory/'assemble.log').open('w') as log:
                    assembled_process=subprocess.run([str((root/matrix['assembler']).resolve()),'-transcript',str(frozen),'-diarization',str(output.resolve()),'-out',str(assembled.resolve())],stdout=log,stderr=subprocess.STDOUT)
                if assembled_process.returncode:
                    receipt['error']='Production assembly failed'
                else:
                    score_path=assembled
            if matrix.get('reference') and not receipt.get('error'):
                try:
                    measured=score(root/matrix['reference'],score_path)
                    (directory/'score.json').write_text(json.dumps(measured,indent=2,default=str,allow_nan=False))
                    receipt['accuracy_status']='scored_against_reviewed_reference'
                except Exception as error:
                    receipt['error']=f'Reference scoring failed: {error}'
        elif result.returncode==0:receipt['error']='successful process produced no output'
        (directory/'receipt.json').write_text(json.dumps(receipt,indent=2))
        results.append(receipt)
    (out/'comparison.json').write_text(json.dumps({'matrix_sha256':digest(matrix_path),'results':results,
      'ranking_status':'requires_human_reference_and_review','accuracy_is_not_label_coverage':True},indent=2))
    return int(any(r['exit_code'] or r.get('error') for r in results))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--matrix',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();raise SystemExit(run(a.matrix,a.out))
