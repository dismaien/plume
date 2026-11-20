import importlib.util
import json
import wave
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


def test_overlap_collapse_is_not_hidden_by_non_overlap_accuracy():
    ref=Annotation(); ref[Segment(0,10),0]='a'; ref[Segment(3,5),1]='b'
    hyp=Annotation(); hyp[Segment(0,10),0]='x'
    result=m.diarization_scores(ref,hyp,Timeline([Segment(0,10)]))
    overlap=result['activity_by_region']['overlap']
    single=result['activity_by_region']['non_overlap']
    assert overlap['reference_speaker_seconds']==4 and overlap['recall']==.5
    assert single['recall']==1
    assert result['missed_speakers']==['b'] and result['worst_speaker_recall']==0


def test_regional_scores_do_not_remap_identity_to_hide_a_voice_swap():
    ref=Annotation(); ref[Segment(0,10),0]='a'; ref[Segment(8,10),1]='b'
    hyp=Annotation(); hyp[Segment(0,8),0]='x'; hyp[Segment(8,10),0]='y'
    result=m.diarization_scores(ref,hyp,Timeline([Segment(0,10)]))
    # Globally x=a and y=b. Re-mapping each overlap person separately would
    # incorrectly give a credit for y as well.
    people=result['activity_by_region']['overlap']['per_speaker']
    assert people['a']['recall']==0 and people['b']['recall']==1


def test_publishing_every_speaker_everywhere_does_not_get_perfect_precision():
    ref=Annotation(); ref[Segment(0,5),0]='a'; ref[Segment(5,10),0]='b'
    hyp=Annotation(); hyp[Segment(0,10),0]='x'; hyp[Segment(0,10),1]='y'
    region=m.diarization_scores(ref,hyp,Timeline([Segment(0,10)]))['activity_by_region']['non_overlap']
    assert region['recall']==1 and region['precision']==.5
    assert region['hypothesis_speaker_seconds']==20


def test_duplicate_tracks_of_one_person_are_not_reference_overlap():
    ref=Annotation();ref[Segment(0,10),0]='a';ref[Segment(2,8),1]='a'
    result=m.diarization_scores(ref,ref,Timeline([Segment(0,10)]))
    assert result['activity_by_region']['overlap']['reference_speaker_seconds']==0
    assert result['activity_by_region']['overlap']['recall'] is None


def test_partial_uem_does_not_turn_a_long_utterance_into_brief_speech():
    ref=Annotation();ref[Segment(0,10),0]='a'
    result=m.diarization_scores(ref,ref,Timeline([Segment(4,4.2)]))
    assert result['brief_turn_reference_seconds']==0 and result['brief_turn_recall'] is None


@pytest.mark.parametrize('unknown',['unknown','Speaker unclear',' UNKNOWN ','   ',None])
def test_unknown_spellings_cannot_earn_person_credit(unknown):
    ref,uem=fixture()
    hyp=m.candidate_annotation({'segments':[dict(start=0,end=10,speaker=unknown)]},'x')
    result=m.diarization_scores(ref,hyp,uem)
    assert result['per_speaker_recall']=={'a':0,'b':0}
    words=[dict(start=0,end=1,word='hello',speaker='a')]
    measured=m.word_scores(words,[dict(start=0,end=1,word='hello',speaker=unknown)],'x',uem)
    assert measured['wer']['error_rate']==0 and measured['cpwer']['error_rate']==1

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

def test_full_text_wer_includes_words_missing_from_alignment():
    reference=[dict(start=0,end=1,word='Hello!',speaker='a'), dict(start=1,end=2,word='world',speaker='a')]
    timed=m.word_scores(reference,reference,'x',Timeline([Segment(0,2)]))
    assert timed['wer']['error_rate']==0
    full=m.full_text_wer(reference,'hello world thank you')
    assert full['insertions']==2 and full['error_rate']==1

def test_full_text_metric_is_only_enabled_for_a_complete_reference(tmp_path):
    audio=tmp_path/'audio.wav'
    with wave.open(str(audio),'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(b'\0\0'*32000)
    (tmp_path/'ref.rttm').write_text('SPEAKER case 1 0 2 <NA> <NA> a <NA> <NA>\n')
    (tmp_path/'ref.uem').write_text('case 1 0 1\n')
    words=[dict(start=.1,end=.5,word='Hello',speaker='a'),dict(start=1.1,end=1.5,word='world',speaker='a')]
    (tmp_path/'words.json').write_text(json.dumps(words))
    manifest=dict(recording_id='case',audio='audio.wav',source_sha256=m.digest(audio),rttm='ref.rttm',uem='ref.uem',
                  words='words.json',split='synthetic_test',reviewed_by_human=True,reviewer='synthetic unit-test fixture, not a benchmark annotation')
    (tmp_path/'ref.json').write_text(json.dumps(manifest))
    (tmp_path/'hyp.json').write_text(json.dumps(dict(text='hello world thank you',word_segments=words,segments=[dict(start=0,end=2,speaker='a')])))
    partial=m.score(tmp_path/'ref.json',tmp_path/'hyp.json')['words']
    assert 'full_text_wer' not in partial
    (tmp_path/'ref.uem').write_text('case 1 0 2\n')
    complete=m.score(tmp_path/'ref.json',tmp_path/'hyp.json')['words']
    assert complete['wer']['error_rate']==0
    assert complete['full_text_wer']['insertions']==2
    assert complete['primary_text_metric']=='full_text_wer'
