#!/usr/bin/env python3
"""Queue-guarded Community-1 baseline using the production adapter script.

The common runner's --model argument is the existing Hugging Face cache root
for this stage. It never downloads, installs packages or supplies a speaker count.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path

from stage_diar_nemotron3 import check_idle


def run(args):
    check_idle(args.db)
    if args.out.exists():raise FileExistsError('Retain the existing immutable candidate')
    os.environ.update(HF_HOME=str(args.model),HUGGINGFACE_HUB_CACHE=str(args.model/'hub'),
                      HF_HUB_CACHE=str(args.model/'hub'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1',PYANNOTE_METRICS_ENABLED='0',
                      PYTORCH_ENABLE_MPS_FALLBACK='1')
    # Match the existing production baseline's explicit per-operation MPS
    # fallback policy. The production helper records it in candidate metadata.
    script=Path(__file__).resolve().parents[2]/'internal/transcription/adapters/py/pyannote/pyannote_diarize.py'
    spec=importlib.util.spec_from_file_location('community_production',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    check_idle(args.db)
    payload=module.diarize_audio(str(args.audio),str(args.out),hf_token='',device=args.device)
    payload['metadata'].update(evaluation_wrapper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                               evaluation_role='unchanged automatic-count production Community-1 baseline')
    args.out.write_text(json.dumps(payload,indent=2,allow_nan=False));args.out.chmod(0o600)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('audio','out','model','db'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--device',choices=['cpu','mps'],required=True)
    run(parser.parse_args())
