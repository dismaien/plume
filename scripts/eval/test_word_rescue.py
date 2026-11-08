import importlib.util
from pathlib import Path
import sys

import numpy as np

from activity_timeline import save_activity

DIRECTORY=Path(__file__).resolve().parents[2]/'internal/transcription/adapters/py/nvidia'
spec=importlib.util.spec_from_file_location('word_rescue',DIRECTORY/'word_rescue.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_only_ambiguous_existing_machine_labels_are_proposed(tmp_path):
    primary={'regular_segments':[dict(start=0,end=3,speaker='speaker_00'),dict(start=1,end=2,speaker='speaker_01')]}
    native={'regular_segments':[dict(start=0,end=1,speaker='speaker_0'),dict(start=2,end=3,speaker='speaker_0'),dict(start=1,end=2,speaker='speaker_1')]}
    logits=np.tile([6.,-6.],(300,1))
    logits[100:200]=[-6.,6.]
    # An opposing model is not sufficient to overwrite clear primary speech.
    logits[50:60]=[-6.,6.]
    native['activity_artifact']=save_activity(tmp_path/'activity.npz',logits,np.ones(300),.01,3.)
    words=[dict(start=.5,end=.6,word='clear',speaker='speaker_00'),
           dict(start=1.1,end=1.2,word='ambiguous',speaker='speaker_00'),
           dict(start=1.3,end=1.4,word='unclear')]
    result=module.propose(primary,native,words)
    assert len(result['proposals'])==1
    proposal=result['proposals'][0]
    assert proposal['index']==1 and proposal['previous']=='speaker_00' and proposal['proposed']=='speaker_01'
    assert words[1]['speaker']=='speaker_00' and 'speaker' not in words[2]


def test_supplemental_numeric_ids_do_not_reuse_primary_letters(tmp_path):
    primary={'regular_segments':[dict(start=0,end=3,speaker='speaker_00'),dict(start=1,end=2,speaker='speaker_01')]}
    # The new short channel has no strong identity match in the primary system.
    native={'regular_segments':[dict(start=0,end=3,speaker='speaker_0'),dict(start=1.2,end=1.3,speaker='speaker_1')]}
    logits=np.tile([6.,-6.],(300,1));logits[120:130]=[-6.,6.]
    native['activity_artifact']=save_activity(tmp_path/'new.npz',logits,np.ones(300),.01,3.)
    result=module.propose(primary,native,[dict(start=1.21,end=1.29,word='reply',speaker='speaker_00')])
    assert result['proposals'][0]['proposed']=='speaker_2'
