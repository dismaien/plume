import importlib.util
from pathlib import Path
from dataclasses import dataclass
from types import SimpleNamespace
import unittest

path = Path(__file__).resolve().parents[2] / 'internal/transcription/adapters/py/whisper_full_audio.py'
spec = importlib.util.spec_from_file_location('whisper_full_audio', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@dataclass
class Segment:
    start: float
    end: float
    text: str


class FullAudioDecoder(unittest.TestCase):
    def args(self):
        return dict(temperature=0, temperature_increment_on_fallback=None,
                    suppress_tokens='-1', task='transcribe', language='de',
                    beam_size=5, best_of=5, patience=1, length_penalty=1,
                    compression_ratio_threshold=2.4, logprob_threshold=-0.5,
                    no_speech_threshold=0.5, condition_on_previous_text=False)

    def test_quiet_late_speech_retains_original_samples_and_timestamps(self):
        original_audio = [0] * (72 * 16000)
        calls = []
        def transcribe(audio, **kwargs):
            self.assertIs(audio, original_audio)
            calls.append(kwargs)
            return iter([Segment(41, 54, 'Quiet speech after the VAD cutoff.')]), SimpleNamespace(language='de')
        result, evidence = module.decode_full_audio(
            SimpleNamespace(model=SimpleNamespace(transcribe=transcribe)), original_audio, self.args())
        self.assertFalse(calls[0]['vad_filter'])
        self.assertFalse(calls[0]['without_timestamps'])
        self.assertTrue(calls[0]['word_timestamps'])
        self.assertEqual(result['segments'][0]['start'], 41)
        self.assertEqual(evidence['input_duration_seconds'], 72)
        self.assertEqual(calls[0]['temperature'], 0)

    def test_decoding_controls_reach_faster_whisper(self):
        received = {}
        def transcribe(audio, **kwargs):
            received.update(kwargs)
            return iter([]), SimpleNamespace(language='en')
        args = self.args()
        args.update(condition_on_previous_text=True, initial_prompt='User vocabulary',
                    logprob_threshold=-0.7, no_speech_threshold=0.4)
        module.decode_full_audio(SimpleNamespace(model=SimpleNamespace(transcribe=transcribe)), [], args)
        self.assertTrue(received['condition_on_previous_text'])
        self.assertEqual(received['initial_prompt'], 'User vocabulary')
        self.assertEqual(received['log_prob_threshold'], -0.7)
        self.assertEqual(received['no_speech_threshold'], 0.4)


if __name__ == '__main__':
    unittest.main()
