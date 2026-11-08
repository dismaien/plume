#!/usr/bin/env python3
"""Read-only inventory. Requested engine is not evidence of executed engine."""
import argparse
import collections
import json
import sqlite3
from pathlib import Path


def inventory(database,limit=0):
    counts=collections.Counter();rows=[]
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as db:
        sql="select id,status,diarization,diarize_model,transcript from transcription_jobs where is_source_part=0 order by updated_at desc"
        if limit:sql+=' limit '+str(int(limit))
        for job_id,status,requested,requested_engine,raw in db.execute(sql):
            counts['recordings']+=1
            if not raw:counts['no_transcript']+=1;continue
            try:payload=json.loads(raw)
            except ValueError:counts['invalid_json']+=1;continue
            if not isinstance(payload,dict):counts['legacy_plain_text']+=1;continue
            words=payload.get('word_segments',[]);segments=payload.get('segments',[])
            units=words or segments
            labelled=sum(bool(u.get('speaker')) for u in units)
            text=payload.get('text') or ''.join(s.get('text','') for s in segments)
            metadata=payload.get('metadata') or {}
            executed=metadata.get('diarization_engine') or metadata.get('diarization_model') or (payload.get('diarization_result') or {}).get('model_used')
            reason=None
            if not text.strip():counts['empty_text_needs_review']+=1;reason='empty_text_not_verified_silence'
            if requested and not labelled:counts['requested_diarization_without_labels']+=1;reason=reason or 'no_speaker_labels'
            if executed:counts['executed_engine_recorded']+=1
            elif requested:counts['executed_engine_unrecorded']+=1
            if reason:rows.append(dict(job_id=job_id,status=status,reason=reason,units=len(units),labelled_units=labelled,
                                      requested_engine=requested_engine,executed_engine=executed))
    return dict(counts=counts,needs_review=rows,policy='read_only; no archive rerun or promotion')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True);p.add_argument('--limit',type=int,default=0);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();result=inventory(a.database,a.limit)
    with a.out.open('x') as handle:json.dump(result,handle,indent=2)
