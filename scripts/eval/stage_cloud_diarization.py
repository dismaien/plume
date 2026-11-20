#!/usr/bin/env python3
"""Opt-in, selected-clip benchmarks for Deepgram v2 and pyannoteAI Precision-3.

Never writes to Plume's live database. Credentials come from an environment
variable or its existing local credential store, never command-line arguments.
Official APIs: https://developers.deepgram.com/docs/diarization and
https://docs.pyannote.ai/tutorials/how-to-upload-files
"""
import argparse
import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from pathlib import Path


def private_json(path,payload):
    with path.open('w') as handle:
        os.chmod(path,0o600)
        json.dump(payload,handle,indent=2,allow_nan=False)


def credential(provider,database):
    variable={'deepgram':'DEEPGRAM_API_KEY','pyannoteai':'PYANNOTE_API_KEY'}[provider]
    if os.getenv(variable): return os.environ[variable]
    if database.exists():
        with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as db:
            row=db.execute('select api_key from cloud_provider_configs where provider=? and is_active=1',(provider,)).fetchone()
        if row and row[0]:return row[0]
    raise ValueError(f'No {provider} credential configured. Add it in local settings or {variable}; do not paste keys into chat.')


def request(url,method='GET',headers=None,payload=None,data=None):
    if urllib.parse.urlsplit(url).scheme!='https':raise ValueError('HTTPS required')
    headers=dict(headers or {})
    if payload is not None:
        data=json.dumps(payload).encode();headers['Content-Type']='application/json'
    req=urllib.request.Request(url,method=method,data=data,headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=60) as response:
            body=response.read()
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        # Do not echo headers, response bodies or signed media URLs into logs.
        raise RuntimeError(f'Provider returned HTTP {error.code}; no automatic submission retry') from None


def canonical(turns):
    # Provider confidence scales are retained in raw.json, not invented here.
    return [dict(start=float(t['start']),end=float(t['end']),speaker=str(t['speaker']).lower())
            for t in turns if t.get('speaker') is not None and float(t['end'])>float(t['start'])]


def run_deepgram(audio,key,call=request):
    query={'model':'nova-3','diarize_model':'v2','utterances':'true','punctuate':'true','detect_language':'true'}
    raw=call('https://api.deepgram.com/v1/listen?'+urllib.parse.urlencode(query),method='POST',
             headers={'Authorization':'Token '+key,'Content-Type':'audio/wav'},data=audio.read_bytes())
    alternatives=raw.get('results',{}).get('channels',[{}])[0].get('alternatives',[{}])[0]
    words=alternatives.get('words',[])
    turns=canonical(raw.get('results',{}).get('utterances',[]))
    output={'text':alternatives.get('transcript',''),'segments':turns,
            'word_segments':[dict(start=w['start'],end=w['end'],word=w.get('punctuated_word',w['word']),
                                  score=w.get('confidence',0),**({'speaker':f"speaker_{w['speaker']}"} if w.get('speaker') is not None else {})) for w in words],
            'metadata':{'diarization_engine':'deepgram/nova-3+diarization-v2','request_config':json.dumps(query,sort_keys=True),
                        'timeline_kind':'joint_asr_utterances_not_independent_vad','provider_model_info':json.dumps(raw.get('metadata',{}),sort_keys=True)}}
    # Use identical speaker IDs in timed words and reported utterances.
    for t in output['segments']:t['speaker']='speaker_'+t['speaker']
    return raw,output


def run_pyannote(audio,key,receipt,out,known_count=None,call=request,sleep=time.sleep):
    headers={'Authorization':'Bearer '+key}
    media='media://plume-eval-'+uuid.uuid4().hex
    location=call('https://api.pyannote.ai/v1/media/input',method='POST',headers=headers,payload={'url':media})
    call(location['url'],method='PUT',headers={'Content-Type':'application/octet-stream'},data=audio.read_bytes())
    config={'url':media,'model':'precision-3','exclusive':True,'turnLevelConfidence':True,'transcription':False}
    if known_count is not None:config['numSpeakers']=known_count
    job=call('https://api.pyannote.ai/v1/diarize',method='POST',headers=headers,payload=config)
    receipt['provider_job_id']=job['jobId'];private_json(out/'receipt.json',receipt)
    deadline=time.monotonic()+900
    while time.monotonic()<deadline:
        raw=call('https://api.pyannote.ai/v1/jobs/'+urllib.parse.quote(job['jobId'],safe=''),headers=headers)
        if raw['status']=='succeeded':break
        if raw['status'] in ('failed','canceled'):raise RuntimeError('Cloud diarization did not succeed; see retained job ID')
        sleep(5)
    else:raise TimeoutError('Cloud job still pending; reuse retained job ID rather than resubmitting')
    result=raw['output'];regular=canonical(result.get('diarization',[]));exclusive=canonical(result.get('exclusiveDiarization',result.get('diarization',[])))
    return raw,{'segments':exclusive,'regular_segments':regular,'metadata':{'diarization_engine':'pyannoteai/precision-3',
                'assignment_timeline':'exclusive','timeline_kind':'speaker_activity_including_overlap',
                'speaker_constraints':json.dumps({'numSpeakers':known_count}), 'confidence_kind':'provider_native_retained_in_raw'}}


def run(provider,audio,out,database,allow_cloud=False,known_count=None):
    if not allow_cloud:raise ValueError('Explicit --allow-cloud required for selected-clip upload')
    with wave.open(str(audio),'rb') as wav:duration=wav.getnframes()/wav.getframerate()
    if duration>300:raise ValueError('Selected-clip benchmark is limited to five minutes; no full-recording upload')
    key=credential(provider,database)
    out.mkdir(parents=True,exist_ok=False)
    receipt={'provider':provider,'audio_sha256':hashlib.sha256(audio.read_bytes()).hexdigest(),'clip_seconds':duration,
             'status':'submitting','authorization_scope':'selected_clip','started_at':time.time()}
    private_json(out/'receipt.json',receipt);started=time.monotonic()
    try:
        if provider=='deepgram':raw,result=run_deepgram(audio,key)
        else:raw,result=run_pyannote(audio,key,receipt,out,known_count)
        private_json(out/'raw.json',raw)
        speakers=sorted({t['speaker'] for t in result['segments']})
        result.update(speakers=speakers,speaker_count=len(speakers),model_used=result['metadata']['diarization_engine'])
        result['metadata']['speaker_identity_status']='accepted' if speakers else 'uncertain'
        private_json(out/'result.json',result)
        if not speakers:raise RuntimeError('Provider returned no speaker labels; this is not successful diarization')
        receipt['status']='succeeded'
    except Exception as error:
        receipt.update(status='failed',error=str(error));raise
    finally:
        receipt['seconds']=time.monotonic()-started;private_json(out/'receipt.json',receipt)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--provider',choices=['deepgram','pyannoteai'],required=True);p.add_argument('--audio',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--allow-cloud',action='store_true')
    p.add_argument('--known-speakers',type=int,help='Only a human-confirmed exact count, used by pyannoteAI')
    p.add_argument('--database',type=Path,default=Path.home()/'Library/Application Support/plume-desktop/data/quill.db')
    a=p.parse_args();run(a.provider,a.audio,a.out,a.database,a.allow_cloud,a.known_speakers)
