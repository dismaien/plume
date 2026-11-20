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


def test_exemplar_votes_protect_mixed_and_overlap_only_tracks():
    from collections import Counter
    spec = importlib.util.spec_from_file_location('speaker_aliases', path.with_name('speaker_aliases.py'))
    aliases = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(aliases)
    panels = [{k: Counter({label: 5}) for k, label in
               [('A','p'), ('alias A','p'), ('B','q'), ('alias B','q'), ('mixed', variable)]}
              for variable in ('p','q','p','q')]
    regular = [{'start': i*10., 'end': i*10.+8, 'speaker': s}
               for i,s in enumerate(['A','alias A','B','alias B','mixed','brief'])]
    mapping = aliases.consensus_aliases(panels, regular)
    assert mapping == {'alias A': 'A', 'alias B': 'B'}
    assert aliases.mixed_tracks(panels, mapping, regular) == {'mixed': ['A', 'B']}
    simultaneous = [dict(start=0, end=.1, speaker=s) for s in ('A', 'B', 'mixed')]
    assert not aliases.mixed_tracks(panels, mapping, simultaneous)
    # A single overlapping reply need never pass the enrolment threshold.
    assert not aliases.reference_panels(regular)
    regular.append({'start':2, 'end':5, 'speaker':'alias A'})
    assert 'alias A' not in aliases.consensus_aliases(panels, regular)
