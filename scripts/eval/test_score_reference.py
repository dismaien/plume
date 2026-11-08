import importlib.util
from pathlib import Path
import pytest
from pyannote.core import Annotation, Segment, Timeline

spec=importlib.util.spec_from_file_location('score_reference',Path(__file__).with_name('score_reference.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def fixture():
    reference=Annotation();reference[Segment(0,8),0]='a';reference[Segment(8,10),0]='b'
    return reference,Timeline([Segment(0,10)])

def test_permuted_names_have_zero_error():
    ref,uem=fixture();hyp=ref.rename_labels({'a':'y','b':'x'})
    result=m.diarization_scores(ref,hyp,uem)
    assert result['0.0']['diarization error rate']==0
    assert result['jer']==0
    assert result['per_speaker_recall']=={'a':1,'b':1}

def test_all_unknown_is_full_miss_not_perfect_precision():
    ref,uem=fixture()
    hyp=m.candidate_annotation({'segments':[dict(start=0,end=10,speaker='')]},'x')
    result=m.diarization_scores(ref,hyp,uem)
    assert result['0.0']['diarization error rate']==1
    assert result['unattributed_reference_fraction']==1
    assert result['per_speaker_recall']=={'a':0,'b':0}

def test_brief_speaker_collapse_is_reported():
    ref,uem=fixture();hyp=Annotation();hyp[Segment(0,10),0]='only'
    result=m.diarization_scores(ref,hyp,uem)
    assert result['false_solo']
    assert result['0.0']['confusion']==2
    assert result['per_speaker_recall']['b']==0

def test_overlap_is_scored():
    ref=Annotation();ref[Segment(0,10),0]='a';ref[Segment(3,5),1]='b'
    hyp=Annotation();hyp[Segment(0,10),0]='a'
    result=m.diarization_scores(ref,hyp,Timeline([Segment(0,10)]))
    assert result['0.0']['total']==12
    assert result['0.0']['missed detection']==2

def test_brief_overlapping_speaker_is_visible_despite_good_overall_score():
    ref=Annotation(); ref[Segment(0,100),0]='dominant'; ref[Segment(4,4.5),1]='brief'
    hyp=Annotation(); hyp[Segment(0,100),0]='only'
    result=m.diarization_scores(ref,hyp,Timeline([Segment(0,100)]))
    assert result['0.0']['diarization error rate'] < .01
    assert result['brief_turn_recall']==0
    assert result['per_speaker_recall']['brief']==0
    missed=result['missed_reference_turns'][0]
    assert missed['speaker']=='brief' and missed['matched_fraction']==0
    assert missed['overlapping_reference_speech']

def test_unknown_words_cannot_win_attribution_metric():
    words=[dict(start=0,end=1,word='hello',speaker='a')]
    score=m.word_scores(words,[dict(start=0,end=1,word='hello')],'x',Timeline([Segment(0,2)]))
    assert score['wer']['error_rate']==0
    assert score['cpwer']['error_rate']==1
    assert score['tcpwer']['error_rate']==1

def test_model_draft_is_rejected(tmp_path):
    manifest=tmp_path/'ref.json';manifest.write_text('{"reviewed_by_human":false}')
    with pytest.raises(ValueError,match='Human review'):
        m.score(manifest,tmp_path/'hyp.json')

def test_primary_wer_is_not_penalized_for_punctuation_case():
    reference=[dict(start=0,end=1,word='Hello!',speaker='a')]
    hypothesis=[dict(start=0,end=1,word='hello',speaker='b')]
    result=m.word_scores(reference,hypothesis,'x',Timeline([Segment(0,2)]))
    assert result['wer']['error_rate']==0
    assert result['cpwer']['error_rate']==0
    assert result['verbatim_wer']['error_rate']==1
