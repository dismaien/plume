import os, sys, tempfile, subprocess, socket, time, sqlite3, json, urllib.request, urllib.error
from pathlib import Path
if len(sys.argv) != 2: raise SystemExit('Usage: python3 scripts/smoke-transcript-editing.py /absolute/path/to/backend')
binary=Path(sys.argv[1]).resolve()
root=Path(tempfile.mkdtemp(prefix='plume-edit-api-'))
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
env=os.environ.copy();env.update(HOST='127.0.0.1',PORT=str(port),APP_ENV='development',AUTH_MODE='local',JWT_SECRET='isolated-local-transcript-edit-test',DATABASE_PATH=str(root/'test.db'),UPLOAD_DIR=str(root/'uploads'),TRANSCRIPTS_DIR=str(root/'transcripts'),TEMP_DIR=str(root/'temp'),WHISPERX_ENV=str(root/'env'),QUILL_DEFER_MODEL_INIT='true',QUILL_SKIP_STARTUP_BACKFILLS='true',QUILL_SKIP_STARTUP_SYNC_MANAGERS='true')
log=open(root/'server.log','w');proc=subprocess.Popen([str(binary)],cwd=root,env=env,stdout=log,stderr=log)
def req(path,body=None):
 r=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=None if body is None else json.dumps(body).encode(),method='GET' if body is None else 'PUT',headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(r,timeout=10) as response:return response.status,json.load(response)
 except urllib.error.HTTPError as e:return e.code,json.load(e)
try:
 for _ in range(60):
  try:
   if req('/health')[0]==200:break
  except Exception:pass
  time.sleep(.25)
 else:raise RuntimeError('Server did not start: '+(root/'server.log').read_text()[-1000:])
 original={'text':'An actual API mistake.','segments':[{'start':0,'end':2,'text':'An actual API mistake.','speaker':'SPEAKER_00'}]}
 with sqlite3.connect(root/'test.db') as c:
  c.execute("INSERT INTO transcription_jobs(id,status,audio_path,transcript) VALUES(?,?,?,?)",('edit-smoke','completed','untouched.wav',json.dumps(original)))
 p='/api/v1/transcription/edit-smoke/transcript'
 status,base=req(p+'/editor');assert status==200,(status,base)
 status,saved=req(p+'/editor',{'expected_version':base['version'],'segment_texts':['An actual API correction.']});assert status==200,(status,saved)
 status,history=req(p+'/revisions');assert status==200 and len(history['revisions'])==2,(status,history)
 original_id=history['revisions'][-1]['id']
 status,old=req(p+'/revisions/'+str(original_id));assert old['transcript']==original
 status,conflict=req(p+'/editor',{'expected_version':base['version'],'segment_texts':['Stale draft']});assert status==409,(status,conflict)
 status,restored=req(p+'/editor',{'expected_version':saved['version'],'restore_id':original_id});assert status==200 and restored['transcript']==original,(status,restored)
 assert req(p+'/revisions')[1]['revisions'][0]['kind']=='restore'
 assert req(p)[1]['transcript']==original
 speaker_original={'text':'Other words. My safety is limited.','segments':[{'start':0,'end':4,'text':'Other words. My safety is ','speaker':'A'},{'start':4,'end':6,'text':'limited.','speaker':'B','speaker_name':'Brett'}]}
 with sqlite3.connect(root/'test.db') as c:
  c.execute("INSERT INTO transcription_jobs(id,status,audio_path,transcript) VALUES(?,?,?,?)",('speaker-smoke','completed','untouched.wav',json.dumps(speaker_original)))
 p='/api/v1/transcription/speaker-smoke/transcript'
 status,base=req(p+'/editor');assert status==200
 status,saved=req(p+'/editor',{'expected_version':base['version'],'segment_parts':[[{'text':'Other words. ','speaker':'A'},{'text':'My safety is ','speaker':'B'}],[{'text':'limited.','speaker':'B'}]]})
 assert status==200,(status,saved)
 assert saved['transcript']['segments'][1]['speaker']=='B'
 assert saved['transcript']['segments'][1]['speaker_name']=='Brett'
 assert saved['transcript']['segments'][1]['timing_approximate'] is True
 history=req(p+'/revisions')[1];assert history['revisions'][0]['kind']=='speaker_edit'
 status,restored=req(p+'/editor',{'expected_version':saved['version'],'restore_id':history['revisions'][-1]['id']})
 assert status==200 and restored['transcript']==speaker_original
 print('PASS: real HTTP text/speaker correction, history, stale conflict, exact restore and read; isolated database:',root)
finally:
 proc.terminate()
 try:proc.wait(timeout=5)
 except subprocess.TimeoutExpired:proc.kill();proc.wait()
 log.close()
