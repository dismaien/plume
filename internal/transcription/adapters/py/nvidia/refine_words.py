#!/usr/bin/env python3
"""Offline Nemotron word refinement; emits proposals, never an edited transcript."""
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from word_rescue import propose,POLICY


def run(args):
    original=Path(args.input).read_bytes();transcript=json.loads(original)
    if args.native_candidate:
        native=json.loads(Path(args.native_candidate).read_text())
    else:
        from nemotron3_core import infer
        native_path=Path(args.out).with_name('native-result.json')
        infer(SimpleNamespace(audio=Path(args.audio),out=native_path,model=Path(args.model),
                              device=args.device,cpu_threads=1,scheduling='Plume word-refinement stage'))
        native=json.loads(native_path.read_text())
    from activity_timeline import replay
    native['regular_segments']=replay(native['activity_artifact'],POLICY['mapping_activity_threshold'])
    primary=transcript.get('diarization_result')
    if not primary:raise ValueError('Primary acoustic timeline is required')
    result=propose(primary,native,transcript.get('word_segments',[]),transcript.get('metadata',{}))
    result.update(input_sha256=hashlib.sha256(original).hexdigest(),
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  model_revision=native.get('metadata',{}).get('model_revision',''),
                  native_candidate=str(args.native_candidate or native_path))
    with Path(args.out).open('x') as output:json.dump(result,output,indent=2,allow_nan=False)
    Path(args.out).chmod(0o600)
    print(f"Proposed {len(result['proposals'])} word speaker refinements; source unchanged")
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('input','out'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--native-candidate',type=Path)
    parser.add_argument('--audio',type=Path)
    parser.add_argument('--model',type=Path)
    parser.add_argument('--device',choices=['mps','cpu'],default='mps')
    run(parser.parse_args())
