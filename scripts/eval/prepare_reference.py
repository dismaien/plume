#!/usr/bin/env python3
"""Prepare a private human-review pack. Draft model labels are never gold."""
import argparse
import hashlib
import json
import shutil
import wave
from pathlib import Path


def prepare(audio, candidate, out, recording_id, split):
    out.mkdir(parents=True,exist_ok=False)
    # Preserve the source and keep the pack portable without touching the archive.
    destination=out/audio.name
    shutil.copy2(audio,destination)
    source_hash=hashlib.file_digest(destination.open('rb'),'sha256').hexdigest()
    with wave.open(str(destination),'rb') as wav:
        duration=wav.getnframes()/wav.getframerate()
    payload=json.loads(candidate.read_text()) if candidate else {}
    diar=payload.get('diarization_result') or payload
    turns=diar.get('regular_segments',diar.get('segments',[]))
    rttm=[]
    for t in turns:
        if t.get('speaker'):
            rttm.append(f"SPEAKER {recording_id} 1 {t['start']:.6f} {t['end']-t['start']:.6f} <NA> <NA> {t['speaker']} <NA> <NA>")
    (out/'draft-model.rttm').write_text('\n'.join(rttm)+'\n')
    (out/'reference.rttm').write_text('# Human annotation required. Do not score draft-model.rttm as ground truth.\n')
    (out/'reference.uem').write_text(f'{recording_id} 1 0 {duration:.6f}\n')
    (out/'draft-model-words.json').write_text(json.dumps(payload.get('word_segments',[]),indent=2))
    manifest={'recording_id':recording_id,'audio':audio.name,'source_sha256':source_hash,
              'rttm':'reference.rttm','uem':'reference.uem','split':split,
              'reviewed_by_human':False,'reviewer':'','annotation_status':'unreviewed',
              'instructions':'Listen and annotate speaker turns including overlap, quiet speech and brief interjections. Correct words/timings separately. Set reviewed_by_human only after listening to every scored UEM interval. Add words filename when reference words are ready.'}
    (out/'reference.json').write_text(json.dumps(manifest,indent=2))
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audio',type=Path,required=True);p.add_argument('--candidate',type=Path)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--recording-id',required=True)
    p.add_argument('--split',choices=['tuning','held_out'],required=True)
    a=p.parse_args();prepare(a.audio,a.candidate,a.out,a.recording_id,a.split)

if __name__=='__main__':main()
