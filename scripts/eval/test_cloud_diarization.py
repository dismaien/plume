import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('cloud',Path(__file__).with_name('stage_cloud_diarization.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_deepgram_pins_v2_without_legacy_flag(tmp_path):
    audio=tmp_path/'a.wav';audio.write_bytes(b'test')
    def call(url,**kwargs):
        assert 'diarize_model=v2' in url and 'diarize=true' not in url
        assert kwargs['data']==b'test'
        return {'results':{'channels':[{'alternatives':[{'transcript':'hello','words':[{'start':0,'end':1,'word':'hello','speaker':0}]}]}],
                           'utterances':[{'start':0,'end':1,'speaker':0}]}}
    raw,result=m.run_deepgram(audio,'test-key',call)
    assert result['segments'][0]['speaker']==result['word_segments'][0]['speaker']=='speaker_0'
    assert 'confidence' not in result['segments'][0]

def test_precision3_upload_preserves_overlap_and_saves_job_id(tmp_path):
    audio=tmp_path/'a.wav';audio.write_bytes(b'test');calls=[];receipt={}
    def call(url,**kwargs):
        calls.append((url,kwargs))
        if url.endswith('/media/input'):return {'url':'https://upload.example/object'}
        if url.startswith('https://upload.example'):assert 'Authorization' not in kwargs['headers'];return {}
        if url.endswith('/diarize'):
            assert kwargs['payload']['model']=='precision-3'
            assert kwargs['payload']['numSpeakers']==2
            assert 'confidence' not in kwargs['payload']
            return {'jobId':'safe-id'}
        return {'status':'succeeded','output':{'diarization':[{'start':0,'end':2,'speaker':'A'},{'start':1,'end':2,'speaker':'B'}],
                  'exclusiveDiarization':[{'start':0,'end':2,'speaker':'A'}]}}
    raw,result=m.run_pyannote(audio,'test-key',receipt,tmp_path,2,call)
    assert receipt['provider_job_id']=='safe-id'
    assert len(result['regular_segments'])==2 and len(result['segments'])==1
    assert 'test-key' not in (tmp_path/'receipt.json').read_text()

def test_cloud_is_opt_in_before_reading_audio(tmp_path):
    with pytest.raises(ValueError,match='allow-cloud'):
        m.run('deepgram',tmp_path/'missing.wav',tmp_path/'out',tmp_path/'missing.db')
