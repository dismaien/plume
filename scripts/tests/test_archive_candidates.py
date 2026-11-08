import contextlib
import dataclasses
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('candidate_worker', SCRIPTS / 'run-archive-candidates.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


@dataclasses.dataclass
class Segment:
    start: float = 0
    end: float = 1
    text: str = 'Recognized speech'


class CandidateWorkerTests(unittest.TestCase):
    def test_more_than_twenty_with_failure_then_restart_skips_terminal_jobs(self):
        self.run_queue()

    def test_optional_retry_keeps_baselines_and_reuses_outputs_on_restart(self):
        self.run_queue(enable_retries=True)

    def test_failed_optional_retry_does_not_hide_completed_baselines(self):
        self.run_queue(enable_retries=True, fail_retries=True)

    def run_queue(self, enable_retries=False, fail_retries=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / 'candidates'
            (root / 'retranscription-runs').mkdir()
            db = root / 'test.db'
            with sqlite3.connect(db) as conn:
                conn.execute('CREATE TABLE transcription_jobs (status TEXT)')
            source = root / 'source.wav'
            source.write_bytes(b'unchanged source')
            original = root / 'original.json'
            original.write_text('{}')
            plan = root / 'plan.json'
            plan.write_text(json.dumps({'db': str(db), 'jobs': [{'job_id': str(i)} for i in range(23)]}))
            backups = []

            def backup(conn, destination, entry):
                backups.append(entry['job_id'])
                if entry['job_id'] == '0':
                    raise ValueError('one unreadable baseline')
                before = {'job': {'audio_path': str(source), 'transcript_json_path': str(original)},
                          'audio_sha256': worker.sha256(source), 'transcript_sha256': worker.sha256(original),
                          'metrics': {'word_count': 100}}
                folder = destination / entry['job_id']
                folder.mkdir()
                worker.write_json(folder / 'snapshot.before.json', before)
                return before, {}

            class Model:
                calls = 0
                options = []

                def __init__(self, *args, **kwargs):
                    pass

                def transcribe(self, *args, **kwargs):
                    Model.calls += 1
                    Model.options.append(kwargs)
                    if fail_retries and kwargs['temperature'] != 0:
                        raise ValueError('optional retry failed')
                    text = 'I remember the name of the medicine but I ' * 12 if enable_retries and not kwargs['vad_filter'] and kwargs['temperature'] == 0 else 'Recognized speech'
                    return iter([Segment(text=text)]), types.SimpleNamespace(language='de')

            modules = {
                'numpy': types.SimpleNamespace(isfinite=lambda x: types.SimpleNamespace(all=lambda: True)),
                'torch': types.SimpleNamespace(set_num_threads=lambda n: None),
                'faster_whisper': types.SimpleNamespace(WhisperModel=Model),
                'faster_whisper.audio': types.SimpleNamespace(decode_audio=lambda *a, **k: [0.0]),
                'faster_whisper.vad': types.SimpleNamespace(VadOptions=lambda **k: k),
            }
            queue = types.SimpleNamespace(backup=backup)
            audit = types.SimpleNamespace(parse_transcript=lambda p: {'word_count': 2})
            argv = ['worker', '--plan', str(plan), '--output', str(output), '--db', str(db)]
            if enable_retries:
                argv.append('--fallback-on-repetition')
            expected_calls = 66 if enable_retries else 44
            with patch.dict(sys.modules, modules), patch.object(sys, 'argv', argv), \
                    patch.object(worker, 'load_script', side_effect=lambda n: queue if n.startswith('queue') else audit), \
                    patch.object(worker.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=20 * 1024**3)), \
                    patch.object(worker.traceback, 'print_exc'), contextlib.redirect_stdout(None):
                worker.main()
                first = json.loads((output / 'manifest.json').read_text())
                self.assertEqual(first['state'], 'queue_complete')
                self.assertEqual(len(first['jobs']), 23)
                self.assertEqual(first['jobs'][0]['state'], 'failed_candidate')
                self.assertTrue(all(j['state'] == 'candidate_unreviewed' for j in first['jobs'][1:]))
                self.assertEqual(Model.calls, expected_calls)
                if enable_retries and not fail_retries:
                    folder = output / '1'
                    baseline = json.loads((folder / 'full_audio.json').read_text())
                    retry = json.loads((folder / 'temperature_fallback.json').read_text())
                    self.assertIn('medicine', baseline['text'])
                    self.assertEqual(retry['text'], 'Recognized speech')
                    self.assertFalse(retry['metadata']['human_reviewed'])
                    self.assertEqual(retry['metadata']['candidate_mode'], 'temperature_fallback')
                    self.assertEqual(retry['metadata']['temperature'], [0, .2, .4, .6, .8, 1])
                    self.assertFalse(Model.options[2]['vad_filter'])
                    self.assertEqual(Model.options[2]['language'], 'de')
                    self.assertEqual(Model.options[0]['temperature'], 0)
                    self.assertTrue(Model.options[1]['vad_filter'])
                if fail_retries:
                    self.assertEqual(first['jobs'][1]['retry_error'], 'optional retry failed')
                    self.assertTrue((output / '1' / 'full_audio.json').exists())
                    self.assertTrue((output / '1' / 'silero_comparison.json').exists())
                    self.assertFalse((output / '1' / 'temperature_fallback.json').exists())
                worker.main()
                self.assertEqual(Model.calls, expected_calls)
                self.assertEqual(len(backups), 23)
                (output / 'STOP').touch()
                worker.main()
                self.assertEqual(json.loads((output / 'manifest.json').read_text())['state'], 'paused')
                self.assertEqual(Model.calls, expected_calls)
            self.assertEqual(source.read_bytes(), b'unchanged source')
            self.assertEqual(original.read_text(), '{}')


if __name__ == '__main__':
    unittest.main()
