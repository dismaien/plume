import json
from pathlib import Path

import numpy as np
import pytest

from activity_timeline import extract_segments,replay,save_activity
from rethreshold_activity import derive


def test_short_simultaneous_voice_survives_and_padding_never_becomes_speech():
    logits=np.array([[3,-3],[3,3],[3,3],[3,-3],[3,3]],dtype=np.float32)
    result=extract_segments(logits,[1,1,1,1,0],.01,.045,.5)
    assert result==[dict(start=0.,end=.04,speaker='speaker_0'),
                    dict(start=.01,end=.03,speaker='speaker_1')]


def test_threshold_replay_changes_activity_without_inventing_person_ids(tmp_path):
    logits=np.array([[3,-3],[3,-.2],[3,-3]],dtype=np.float32)
    artifact=save_activity(tmp_path/'activity.npz',logits,[1,1,1],.01,.03)
    candidate=tmp_path/'base.json'
    candidate.write_text(json.dumps(dict(model='synthetic_fixture_not_a_benchmark',activity_artifact=artifact,
                                         metadata={'test_only':'true'})))
    original=replay(artifact,.5)
    assert len(original)==1
    out=tmp_path/'threshold.json';result=derive(candidate,.4,out)
    assert result['speaker_count']==2 and result['regular_segments'][1]['speaker']=='speaker_1'
    assert result['metadata']['test_only']=='true'
    with pytest.raises(FileExistsError):derive(candidate,.4,out)
    assert json.loads(candidate.read_text())['model']=='synthetic_fixture_not_a_benchmark'


def test_modified_activity_is_rejected_before_scoring(tmp_path):
    artifact=save_activity(tmp_path/'activity.npz',np.zeros((2,1)),[1,1],.01,.02)
    with Path(artifact['path']).open('ab') as out:out.write(b'changed')
    with pytest.raises(ValueError,match='artifact changed'):replay(artifact,.5)


@pytest.mark.parametrize('threshold',[0,1,float('nan')])
def test_invalid_threshold_is_rejected(threshold):
    with pytest.raises(ValueError,match='threshold'):extract_segments(np.zeros((1,1)),[1],.01,.01,threshold)


def test_invalid_model_outputs_are_never_published():
    with pytest.raises(ValueError,match='scores'):extract_segments(np.array([[float('nan')]]),[1],.01,.01,.5)
    with pytest.raises(ValueError,match='shape'):extract_segments(np.zeros((2,1)),[1],.01,.02,.5)
