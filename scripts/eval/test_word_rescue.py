import importlib.util
from pathlib import Path
import sys

import numpy as np

from activity_timeline import save_activity

DIRECTORY=Path(__file__).resolve().parents[2]/'internal/transcription/adapters/py/nvidia'
sys.path.insert(0, str(DIRECTORY))
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


def test_unmatched_channel_is_only_a_review_suggestion(tmp_path):
    primary={'regular_segments':[dict(start=0,end=3,speaker='speaker_00'),dict(start=1,end=2,speaker='speaker_01')]}
    # The new short channel has no strong identity match in the primary system.
    native={'regular_segments':[dict(start=0,end=3,speaker='speaker_0'),dict(start=1.2,end=1.3,speaker='speaker_1')]}
    logits=np.tile([6.,-6.],(300,1));logits[120:130]=[-6.,6.]
    native['activity_artifact']=save_activity(tmp_path/'new.npz',logits,np.ones(300),.01,3.)
    result=module.propose(primary,native,[dict(start=1.21,end=1.29,word='reply',speaker='speaker_00')])
    proposal, = result['proposals']
    assert proposal['index'] == 0 and proposal['proposed'] == ''
    assert proposal['review_channel'] == 'speaker_1'
    assert 'speaker_1' not in result['mapping']


def test_unmatched_channel_can_confirm_an_existing_overlap_only_voice(tmp_path):
    primary = {
        'segments': [dict(start=0, end=3, speaker='A')],
        'regular_segments': [dict(start=0, end=3, speaker='A'), dict(start=1.2, end=1.3, speaker='B')],
    }
    native = {'regular_segments': [dict(start=0, end=3, speaker='speaker_0'),
                                   dict(start=1, end=2, speaker='speaker_1')]}
    logits = np.tile([6., -6.], (300, 1))
    logits[120:130] = [-6., 6.]
    native['activity_artifact'] = save_activity(tmp_path/'brief.npz', logits, np.ones(300), .01, 3.)
    words = [dict(start=1.21, end=1.29, word='reply', speaker='A')]
    proposal, = module.propose(primary, native, words)['proposals']
    assert proposal['proposed'] == 'B' and 'review_channel' not in proposal

    # Two indistinguishable overlapping voices cannot identify this reply.
    primary['regular_segments'].append(dict(start=1.2, end=1.3, speaker='C'))
    proposal, = module.propose(primary, native, words)['proposals']
    assert proposal['proposed'] == '' and proposal['review_channel'] == 'speaker_1'


def test_mixed_activity_preserves_unsupported_music_and_competing_replies():
    primary = {'metadata': {'speaker_mixed_tracks': '{"X":["A","B"]}'},
               'mixed_segments': [dict(start=0,end=1,speaker='X'),dict(start=4,end=5,speaker='X')]}
    native = {'segments': [dict(start=.1,end=.6,speaker='c'),dict(start=.7,end=.9,speaker='d')]}
    got = module.recover_mixed_activity(primary, native, {'c':'A','d':'B'})
    assert [(r['speaker'],r['start'],r['end']) for r in got['mixed_segments']] == [
        ('A', .1, .6), ('B', .7, .9)]
    assert all(r['index'] == 0 for r in got['mixed_segments'])


def test_two_simultaneous_known_voices_remain_in_regular_activity():
    region = dict(start=0, end=1, speaker='X')
    primary = {'metadata': {'speaker_mixed_tracks': '{"X":["A","B"]}'},
               'mixed_segments': [region], 'mixed_regular_segments': [region]}
    native = {'segments': [dict(start=0,end=1,speaker='c'),dict(start=.5,end=.8,speaker='d')]}
    result = module.recover_mixed_activity(primary, native, {'c':'A','d':'B'})
    summarize = lambda rows: {(r['start'], r['end'], r['speaker']) for r in rows}
    solo = {(0,.5,'A'),(.8,1,'A')}
    assert summarize(result['mixed_segments']) == solo
    assert summarize(result['mixed_regular_segments']) == solo | {(.5,.8,'A'),(.5,.8,'B')}


def test_local_channel_identity_can_change_without_filling_silence_or_disputes():
    from gap_evidence import local_voice_evidence, gap_proposals
    # A globally identical channel carries different voices in distant excerpts.
    turns = [dict(start=0, end=2, speaker='A'), dict(start=122, end=124, speaker='B')]
    primary = dict(segments=turns, regular_segments=turns)
    p = np.zeros((12500, 2))
    p[:250, 0] = .99
    p[12150:12400, 0] = .99
    words = [dict(start=2, end=2.5, word='first'), dict(start=60, end=60.3, word='silence'),
             dict(start=121.5, end=122, word='reply'),
             dict(start=121.5, end=122, word='disputed', speaker_review=True),
             dict(start=121.5, end=122, word='saved', speaker='A')]
    evidence = local_voice_evidence(primary, p, np.ones(len(p), dtype=bool), .01)
    proposals = gap_proposals(evidence, p, .01, words, {})
    assert [(w['word'], w['proposed']) for w in proposals] == [('first', 'A'), ('reply', 'B')]


def test_decoder_window_matches_word_end_across_restored_pause():
    import json
    from gap_evidence import decoder_windows
    metadata = dict(vad_retained_speech_regions='[{"start":0,"end":3},{"start":12,"end":15}]')
    words = [dict(word='the', start=.8, end=1), dict(word='key', start=1, end=13)]
    tokens = [dict(word=' the', start=.8, end=1, probability=.99),
              dict(word=' key', start=3.65, end=4, probability=.99)]
    data = dict(decoder='faster_whisper_full_audio', input_duration_seconds=6, segments=[dict(words=tokens)])
    metadata['asr_diagnostics'] = json.dumps(data)
    assert decoder_windows(metadata, words)[1] == (12.65, 13)
    data['input_duration_seconds'] = 15
    metadata['asr_diagnostics'] = json.dumps(data)
    assert decoder_windows(metadata, words) == {}
    assert decoder_windows(dict(asr_diagnostics='{'), words) == {}


def test_clipped_syllable_on_overlap_preserves_a_separate_acknowledgement():
    from gap_evidence import local_voice_evidence, clipped_word_proposal
    primary = dict(segments=[dict(start=0, end=2, speaker='A'), dict(start=2, end=2.16, speaker='B'),
                             dict(start=2.16, end=3, speaker='A')],
                   regular_segments=[dict(start=0, end=3, speaker='A'), dict(start=1.95, end=2.21, speaker='B')])
    p = np.tile([.99, .01], (300, 1))
    p[195:221, 1] = .95
    evidence = local_voice_evidence(primary, p, np.ones(300, dtype=bool), .01)
    words = [dict(word='the', start=1.24, end=1.36, speaker='A'),
             dict(word='decision.', start=1.99, end=2.17, speaker='B', score=.1)]
    assert clipped_word_proposal(1, words, (1.36, 2.16), evidence, p)['proposed'] == 'A'
    words[1]['word'] = 'Yeah.'
    assert clipped_word_proposal(1, words, (1.36, 2.16), evidence, p) is None


def test_word_crossing_vad_cut_only_counts_audio_passed_to_asr():
    from gap_evidence import local_voice_evidence, gap_proposals
    turns = [dict(start=2, end=6, speaker='B'), dict(start=7.9, end=10, speaker='A')]
    primary = dict(segments=turns, regular_segments=turns)
    p = np.zeros((1000, 2))
    p[200:600, 1] = .99
    p[790:, 0] = .99
    ev = local_voice_evidence(primary, p, np.ones(1000, bool), .01)
    words = [dict(start=.65, end=8.2, word='when'), dict(start=8.2, end=8.6, word='ready', speaker='A')]
    metadata = dict(vad_retained_speech_regions='[{"start":0,"end":1},{"start":8,"end":10}]')
    got = gap_proposals(ev,p,.01,words,metadata)
    assert [(x['index'],x['proposed']) for x in got] == [(0,'A')]
    assert not gap_proposals(ev,p,.01,words,{})
    # The same competing voice counts if the recognizer actually heard it.
    metadata['vad_retained_speech_regions'] = '[{"start":0,"end":3},{"start":8,"end":10}]'
    assert not gap_proposals(ev,p,.01,words,metadata)


def test_native_reply_requires_a_separate_locally_identified_turn():
    from gap_evidence import local_voice_evidence, native_reply_proposal
    turns = [dict(start=0, end=1.02, speaker='A'), dict(start=2, end=3.5, speaker='A'),
             dict(start=4, end=10, speaker='B')]
    primary = dict(segments=turns, regular_segments=turns)
    p = np.zeros((1000, 2))
    p[:100,0] = p[200:350,0] = .98
    p[117:168,1] = p[400:,1] = .99
    words = [dict(start=.6,end=1,word='asking',speaker='A'),dict(start=1,end=1.52,word='yeah'),
             dict(start=2,end=2.4,word='then',speaker='A')]
    ev = local_voice_evidence(primary,p,np.ones(len(p),bool),.01)
    assert native_reply_proposal(1,words,ev,p,.01)['proposed'] == 'B'
    words[2]['start'] = 1.38
    assert native_reply_proposal(1,words,ev,p,.01) is None


def test_overlap_only_identity_requires_both_sides_of_isolated_reply(tmp_path):
    primary = dict(segments=[dict(start=0,end=5,speaker='A')], regular_segments=[
        dict(start=0,end=5,speaker='A'), dict(start=3,end=3.14,speaker='B')])
    turns = [dict(start=0,end=1,speaker='speaker_0'), dict(start=1.25,end=1.55,speaker='speaker_1'),
             dict(start=2,end=5,speaker='speaker_0'), dict(start=3,end=3.14,speaker='speaker_1'),
             dict(start=4,end=4.45,speaker='speaker_1')]
    logits = np.full((500,2),-7.)
    for t in turns:
        logits[round(t['start']*100):round(t['end']*100),int(t['speaker'][-1])] = 7.
    native = dict(regular_segments=turns, activity_artifact=save_activity(tmp_path/'reply.npz',logits,np.ones(500),.01,5))
    words = [dict(start=.6,end=1,word='finished',speaker='A'),
             dict(start=1.25,end=1.56,word='yeah',speaker='A'),
             dict(start=2,end=2.4,word='and',speaker='A')]
    props = module.propose(primary,native,words)['proposals']
    assert [(p['index'],p['proposed']) for p in props] == [(1,'B')]
    primary['regular_segments'].append(dict(start=3,end=3.14,speaker='C'))
    assert not module.propose(primary,native,words)['proposals']


def test_short_decode_needs_word_and_an_independent_timed_neighbor():
    from short_word_times import matches
    words = [dict(word='right',start=1,end=1.15),dict(word='there',start=1.2,end=1.45)]
    observed = [dict(word=' Right,',start=.02,end=.19,probability=.99),
                dict(word=' there.',start=.2,end=.45,probability=.95)]
    assert matches(words,0,observed,1) == [(1.02,1.19)]
    observed[1]['start'],observed[1]['end']=1.3,1.55
    assert not matches(words,0,observed,1)


def test_short_excerpt_competing_native_activity_vetoes_identity():
    from short_word_times import NativeWordClock, native_witness
    frames = (np.arange(20) + .5) * .01
    voice = np.zeros(20, dtype=int)
    disputed = np.zeros(20, dtype=bool)
    clock = NativeWordClock(['speaker_a', 'speaker_b'], frames, voice, disputed, np.zeros(20, dtype=int), .01)
    assert native_witness(clock, 0., .15) == 'speaker_a'
    assert native_witness(clock, 0., .04) is None
    disputed[6] = True
    assert native_witness(clock, 0., .15) is None
    disputed[6] = False
    voice[8] = 1
    assert native_witness(clock, 0., .15) is None
    voice[8] = 0
    voice[:5] = -1
    clock.channels[:5] = -1
    assert native_witness(clock, 0., .15) == 'speaker_a'
    clock.channels[4] = 1
    assert native_witness(clock, 0., .15) is None


def test_extra_excerpt_targets_preserve_original_clip_context():
    from short_word_times import NativeWordClock, windows
    turns = [dict(start=2., end=4., speaker='A'), dict(start=4., end=5., speaker='B')]
    t = dict(word_segments=[dict(word='one', start=2.4, end=2.6),
                            dict(word='two', start=3.9, end=4.2)],
             diarization_result=dict(regular_segments=turns))
    frames = (np.arange(600) + .5) * .01
    clock = NativeWordClock(['A', 'B'], frames, np.zeros(600, dtype=int), np.zeros(600, dtype=bool), np.zeros(600, dtype=int), .01)
    original = windows(t, 6.)
    expanded = windows(t, 6., clock)
    assert expanded[:len(original)] == original
    assert expanded[len(original):] and all(ids == {1} for _, _, ids in expanded[len(original):])
