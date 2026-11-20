#!/usr/bin/env python3
"""Install the pinned local word-refinement runtime, without touching the archive.

Requires an already downloaded, verified public model. The current ASR and
Community-1 environments are never modified. The application copies its own
versioned scripts when a fresh processing job uses the feature.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

REVISION='f667ed73aee57d40cc39428eb768b4fd87a0a29e'


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def prepare(args):
    root=args.root.resolve();seed=args.model_source.resolve()
    if root.name!='nemotron3':raise ValueError('Use a separate named nemotron3 runtime directory')
    provenance=json.loads((seed/'provenance.json').read_text())
    if provenance['revision']!=REVISION:raise ValueError('Unexpected model revision')
    for name,expected in provenance['files'].items():
        if sha(seed/name)!=expected:raise ValueError('Cached model hash mismatch')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock=args.lock.resolve();lock_hash=sha(lock)
    environment=root/'.venv'
    if not (environment/'bin/python').exists():
        subprocess.run(['uv','venv','--python','3.11',str(environment)],check=True)
    subprocess.run(['uv','pip','sync','--python',str(environment/'bin/python'),'--index-url','https://pypi.org/simple',str(lock)],check=True)
    model=root/'model'
    if not model.exists():
        temporary=Path(tempfile.mkdtemp(prefix='.model-',dir=root))
        try:
            for name in [*provenance['files'],'provenance.json']:shutil.copy2(seed/name,temporary/name)
            for name,expected in provenance['files'].items():
                if sha(temporary/name)!=expected:raise ValueError('Copied model hash mismatch')
            temporary.rename(model)
        finally:
            if temporary.exists():shutil.rmtree(temporary)
    for name,expected in provenance['files'].items():
        if sha(model/name)!=expected:raise ValueError('Installed model differs from pinned source')
    shutil.copy2(lock,root/'requirements.lock.txt')
    profile=dict(enabled=args.enable,model_revision=REVISION,policy='retained_audio_word_gaps_v5',
                 requirements_sha256=lock_hash,model_provenance_sha256=sha(model/'provenance.json'),
                 device='mps',cpu_threads=1,network_inference=False,
                 limitations='Conservative ambiguous-word refinement; not a standalone engine winner or proof of personal-memo accuracy')
    temporary=root/'ready.writing.json';temporary.write_text(json.dumps(profile,indent=2));temporary.chmod(0o600);temporary.replace(root/'ready.json')
    print(json.dumps({'status':'ready','root':str(root),'enabled':args.enable,'model_revision':REVISION},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--model-source',type=Path,required=True)
    parser.add_argument('--lock',type=Path,default=Path(__file__).resolve().parents[1]/'internal/transcription/adapters/py/nvidia/nemotron3-requirements.lock')
    parser.add_argument('--enable',action='store_true')
    prepare(parser.parse_args())
