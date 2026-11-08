"""The model may detect a quiet voice that its exclusive timeline omits."""
import importlib.util
from pathlib import Path

path = Path(__file__).resolve().parents[2] / 'internal/transcription/adapters/py/pyannote/pyannote_diarize.py'
spec = importlib.util.spec_from_file_location('community_contract', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_overlap_only_speaker_survives_the_inventory():
    regular = [{'speaker': 'a'}, {'speaker': 'b'}]
    exclusive = [{'speaker': 'a'}]
    assert module.speaker_inventory(regular, exclusive) == ['a', 'b']


def test_empty_ids_are_not_people_and_input_is_unchanged():
    regular = [{'speaker': ''}, {'speaker': 'b'}, {'speaker': 'b'}]
    assert module.speaker_inventory(regular, []) == ['b']
    assert regular[0]['speaker'] == '' and len(regular) == 3
