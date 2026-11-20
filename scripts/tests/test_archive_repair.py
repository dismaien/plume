import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archive_repair import Ledger, evaluate, load_script, local_request


def snap(**changes):
    m = dict(word_count=1000, legacy_score=0, phrase_runs=0, max_repeated_tokens=0,
             suspicious_thanks=0, duration=600, speech_seconds=500, speaker_count=2,
             dominant_ratio=.6, switches_per_hour=100, unlabeled_ratio=0)
    m.update(changes)
    return dict(job=dict(status='completed', title='My title'), metrics=m,
                mappings=[], classification=['No automated flag',0,[],[],''])


class Gates(unittest.TestCase):
    def test_catastrophic_ngram_not_legacy_score(self):
        b, a = snap(max_repeated_tokens=200), snap(max_repeated_tokens=100)
        self.assertEqual(evaluate(b,a)['state'],'rejected')

    def test_removing_loop_is_improvement_but_needs_api(self):
        self.assertEqual(evaluate(snap(max_repeated_tokens=200),snap())['state'],'completed_unreviewed')

    def test_empty_never_passes(self):
        self.assertEqual(evaluate(snap(),snap(word_count=0))['state'],'rejected')

    def test_unreadable_never_passes(self):
        a=snap();a['metrics']=None
        self.assertEqual(evaluate(snap(),a)['state'],'rejected')

    def test_shorter_needs_review_even_when_loop_removed(self):
        r=evaluate(snap(max_repeated_tokens=200),snap(word_count=100))
        self.assertEqual(r['state'],'improved_review')
        self.assertIn('Word count',r['reasons'][0])

    def test_speech_coverage(self):
        r=evaluate(snap(max_repeated_tokens=200),snap(speech_seconds=50))
        self.assertTrue(any('coverage' in x for x in r['reasons']))

    def test_title_loss_is_rejected(self):
        a=snap();a['job']['title']='Changed'
        self.assertEqual(evaluate(snap(max_repeated_tokens=200),a)['state'],'rejected')

    def test_labels_do_not_prove_identity(self):
        b,a=snap(max_repeated_tokens=200),snap()
        m=dict(original_speaker='S0',custom_name='Person A',match_source='manual',contact_id=1)
        b['mappings']=a['mappings']=[m]
        self.assertEqual(evaluate(b,a)['state'],'improved_review')

    def test_collapse_and_oversegmentation(self):
        for a in (snap(speaker_count=1,dominant_ratio=1),snap(speaker_count=8),snap(switches_per_hour=700)):
            self.assertNotEqual(evaluate(snap(dominant_ratio=.99),a,2,'speakers')['state'],'completed_unreviewed')

    def test_missing_third_speaker(self):
        self.assertEqual(evaluate(snap(speaker_count=1),snap(),3,'speakers')['state'],'improved_review')

    def test_failed_does_not_become_success(self):
        a=snap();a['job']['status']='failed'
        self.assertEqual(evaluate(snap(),a)['state'],'failed')

    def test_remote_api_rejected_without_request(self):
        with self.assertRaises(ValueError):local_request('https://example.com','/private',{'text':'private'})


class Parsing(unittest.TestCase):
    def parse(self,payload):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'transcript.json';p.write_text(json.dumps(payload))
            return load_script('build-archive-quality-audit').parse_transcript(p)

    def test_union_coverage_does_not_double_count_overlap(self):
        m=self.parse({'segments':[{'start':0,'end':10,'text':'hello'}, {'start':5,'end':15,'text':'again'}]})
        self.assertEqual(m['speech_seconds'],15)

    def test_invalid_timing_is_a_metric_not_a_parse_failure(self):
        m=self.parse({'segments':[{'start':5,'end':1,'text':'word'}]})
        self.assertEqual(m['invalid_timing_count'],1)
        self.assertEqual(m['speech_seconds'],0)

    def test_german_filler_with_implausible_timing_needs_review(self):
        m=self.parse({'segments':[{'start':52.45,'end':61.55,'text':'Vielen Dank.'}]})
        self.assertEqual(m['suspicious_thanks'],0)
        self.assertEqual(m['suspicious_fillers'],1)
        a=snap();a['metrics'].update(m)
        self.assertNotEqual(evaluate(snap(max_repeated_tokens=200),a)['state'],'completed_unreviewed')

    def test_plausibly_timed_german_thanks_is_not_flagged(self):
        m=self.parse({'segments':[{'start':0,'end':1.5,'text':'Vielen Dank.'}]})
        self.assertEqual(m['suspicious_fillers'],0)

    def test_english_filler_keeps_legacy_metric(self):
        m=self.parse({'segments':[{'start':0,'end':9,'text':'Thank you.'}]})
        self.assertEqual(m['suspicious_thanks'],1)
        self.assertEqual(m['suspicious_fillers'],1)

    def test_malformed_and_invalid_timing(self):
        for payload in ([],{}, {'segments':[None]}, {'segments':[{'start':0,'end':float('nan')}]}):
            with self.subTest(payload=payload),self.assertRaises((ValueError,TypeError)):
                self.parse(payload)


class State(unittest.TestCase):
    def row(self):
        return dict(job_id='test',severity='Critical',categories='text',status='completed',
                    audio_exists='True',human_speaker_mappings='0',model='large-v3',diarize_model='sherpa-onnx',
                    duration_seconds='600',known_people_count='2',speaker_count='1',dominant_speaker_ratio='1',
                    known_people='Person A, Person B',risk_score='100')

    def test_accepted_persists_across_sync_and_is_not_selected(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(Path(d)/'ledger.db');self.addCleanup(l.conn.close);r=self.row();l.sync([r]);l.record(d,'test','accepted',{'reasons':['passed']});l.sync([r])
            args=SimpleNamespace(severity=['Critical'],category='text',model=None,diarizer=None,human='any',prior_run=None,verdict=None,max_duration_hours=6,limit=20,max_hours=6)
            selected,_=load_script('queue-archive-quality-rerun').select([r],l,args,{})
            self.assertEqual(selected,[])
            self.assertEqual(l.state('test'),'accepted')

    def test_legacy_repetition_pass_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'run';p.mkdir();(p/'manifest.json').write_text(json.dumps({'jobs':[{'job_id':'test','queued':True}]}))
            l=Ledger(Path(d)/'ledger.db');self.addCleanup(l.conn.close);l.sync([self.row()]);l.import_legacy(d);l.import_legacy(d)
            self.assertEqual(len(l.history('test')),1)
            self.assertNotEqual(l.state('test'),'accepted')

    def test_large_v3_is_eligible_and_caps_are_honored(self):
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(Path(d)/'ledger.db');self.addCleanup(l.conn.close);r=self.row();l.sync([r])
            args=SimpleNamespace(severity=['Critical'],category='text',model='large-v3',diarizer=None,human='any',prior_run=None,verdict=None,max_duration_hours=6,limit=20,max_hours=6)
            selected,hours=load_script('queue-archive-quality-rerun').select([r],l,args,{})
            self.assertEqual(len(selected),1);self.assertAlmostEqual(hours,1/6)
            args.max_hours=.1
            self.assertEqual(load_script('queue-archive-quality-rerun').select([r],l,args,{})[0],[])




class Dispatch(unittest.TestCase):
    def test_uncertain_post_persists_intent_and_does_not_retry(self):
        module = load_script('queue-archive-quality-rerun')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            row=State().row()
            ledger=Ledger(root/'ledger.db');self.addCleanup(ledger.conn.close);ledger.sync([row])
            db=root/'live.db';db.touch()
            job={**row,'kind':'text','block_reason':'','expected_speakers':0,'max_speakers':4,'audio_duration_seconds':600}
            plan=root/'plan.json';plan.write_text(json.dumps({'schema_version':2,'db':str(db.resolve()),'jobs':[job]}))
            args=SimpleNamespace(apply_plan=plan,db=db,runs_dir=root/'runs',ledger=root/'ledger.db',api_base='http://127.0.0.1:1')
            with patch.object(module,'read_db'), patch.object(module,'assert_idle'), patch.object(module,'backup',return_value=({}, {'snapshot_before':'saved'})), patch.object(module,'local_request',side_effect=TimeoutError('uncertain')) as post:
                with self.assertRaisesRegex(RuntimeError,'uncertain'):
                    module.apply_plan(args)
                self.assertEqual(post.call_count,1)
            self.assertEqual(ledger.state('test'),'queued')
            manifest=json.loads(next((root/'runs').glob('*/manifest.json')).read_text())
            self.assertEqual(manifest['jobs'][0]['backup']['snapshot_before'],'saved')
            self.assertIn('dispatch_error',manifest['jobs'][0])
            self.assertFalse(manifest['jobs'][0]['queued'])

    def test_backup_failure_prevents_post(self):
        module=load_script('queue-archive-quality-rerun')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);db=root/'live.db';db.touch()
            ledger=Ledger(root/'ledger.db');self.addCleanup(ledger.conn.close);ledger.sync([State().row()])
            job={**State().row(),'kind':'text','block_reason':'','expected_speakers':0,'max_speakers':4,'audio_duration_seconds':600}
            plan=root/'plan.json';plan.write_text(json.dumps({'schema_version':2,'db':str(db.resolve()),'jobs':[job]}))
            args=SimpleNamespace(apply_plan=plan,db=db,runs_dir=root/'runs',ledger=root/'ledger.db',api_base='http://127.0.0.1:1')
            with patch.object(module,'read_db'), patch.object(module,'assert_idle'), patch.object(module,'backup',side_effect=ValueError('missing immutable baseline')), patch.object(module,'local_request') as post:
                with self.assertRaisesRegex(ValueError,'baseline'): module.apply_plan(args)
                post.assert_not_called()
            self.assertEqual(ledger.state('test'),'not_attempted')

    def test_unverified_speaker_plan_is_blocked_before_dispatch(self):
        module=load_script('queue-archive-quality-rerun')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);db=root/'live.db'
            job={'job_id':'test','kind':'speakers','expected_speakers':0,'block_reason':'','duration_seconds':600}
            plan=root/'plan.json';plan.write_text(json.dumps({'schema_version':2,'db':str(db.resolve()),'jobs':[job]}))
            with patch.object(module,'local_request') as post:
                with self.assertRaisesRegex(ValueError,'reviewed evidence'):
                    module.apply_plan(SimpleNamespace(apply_plan=plan,db=db))
                post.assert_not_called()

if __name__=='__main__':unittest.main()
