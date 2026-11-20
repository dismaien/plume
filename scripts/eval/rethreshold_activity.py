#!/usr/bin/env python3
"""Derive an immutable threshold candidate from saved native frame activity.

Select settings on tuning only, freeze them, then evaluate a separate meeting.
No model inference, new identities, reference access or live archive writes.
"""
import argparse
import json
from pathlib import Path

from activity_timeline import file_hash,replay


def derive(candidate, threshold, out):
    source=json.loads(Path(candidate).read_text())
    turns=replay(source['activity_artifact'],threshold)
    speakers=sorted({t['speaker'] for t in turns})
    result={**source,'segments':turns,'regular_segments':turns,
            'speakers':speakers,'speaker_count':len(speakers),
            'metadata':{**source.get('metadata',{}),'frame_activity_threshold':str(threshold),
                        'threshold_parent_sha256':file_hash(candidate),
                        'threshold_script_sha256':file_hash(__file__),
                        'threshold_selection':'experimental; freeze tuning choice before held-out scoring',
                        'speaker_identity_status':'accepted' if speakers else 'uncertain'}}
    with Path(out).open('x') as output:json.dump(result,output,indent=2,allow_nan=False)
    Path(out).chmod(0o600)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--threshold',type=float,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();derive(args.candidate,args.threshold,args.out)
