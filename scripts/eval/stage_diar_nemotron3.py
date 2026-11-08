#!/usr/bin/env python3
"""Queue-guarded evaluation wrapper around the application inference core."""
import argparse
import os
from pathlib import Path
import sys

from scheduling import execution_slot

CORE=Path(__file__).resolve().parents[2]/'internal/transcription/adapters/py/nvidia'
sys.path.insert(0,str(CORE))
from nemotron3_core import canonical_segments,infer


def check_idle(database, device=None):
    if not execution_slot(database,device,os.environ.get('PLUME_EVAL_CPU_ASR_PERMIT')):
        raise RuntimeError('Foreground Plume queue is active; do not load a benchmark model')


def run(args):
    check_idle(args.db,getattr(args,'device',None))
    args.cpu_threads=1 if os.environ.get('PLUME_EVAL_CPU_ASR_PERMIT') else 4
    args.scheduling='bounded_gpu_beside_cpu_asr' if os.environ.get('PLUME_EVAL_CPU_ASR_PERMIT') else 'idle_queue'
    return infer(args,before_model=lambda:check_idle(args.db,args.device))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('audio','out','model','db'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--device',choices=['cpu','mps'],default='mps')
    run(parser.parse_args())
