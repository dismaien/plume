"""Published-reference preparation must preserve clipping and overlap."""
import importlib.util
import json
from pathlib import Path
import wave

import pytest
from score_reference import digest

spec = importlib.util.spec_from_file_location('public_benchmark', Path(__file__).with_name('public_benchmark.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_bad_source_is_rejected_before_reference_creation(tmp_path):
    (tmp_path / 'IS1009a.Mix-Headset.wav').write_bytes(b'not the original audio')
    with pytest.raises(ValueError, match='waveform hash mismatch'):
        module.prepare(tmp_path, tmp_path / 'out')


def test_reference_crop_preserves_overlap_and_uses_relative_time(tmp_path, monkeypatch):
    source = tmp_path / 'source'; source.mkdir()
    audio = source / 'IS1009a.Mix-Headset.wav'
    with wave.open(str(audio), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(b'\0\0' * 16000 * 4)
    annotation = source / 'IS1009a.rttm'
    annotation.write_text('SPEAKER original 1 0.5 2 <NA> <NA> a <NA> <NA>\n'
                          'SPEAKER original 1 1.5 1.0 <NA> <NA> b <NA> <NA>\n')
    monkeypatch.setattr(module, 'AUDIO_HASH', digest(audio))
    monkeypatch.setattr(module, 'RTTM_HASH', digest(annotation))
    monkeypatch.setattr(module, 'EXCERPTS', [('crop', 1, 3)])
    module.prepare(source, tmp_path / 'out')
    crop = tmp_path / 'out/crop'
    rows = [line.split() for line in (crop / 'reference.rttm').read_text().splitlines()]
    assert [(float(row[3]), float(row[4]), row[7]) for row in rows] == [(0, 1.5, 'a'), (.5, 1, 'b')]
    manifest = json.loads((crop / 'reference.json').read_text())
    assert manifest['source_sha256'] == digest(crop / 'audio.wav')
    assert manifest['annotation_origin'] == 'published_manual_annotations_not_reviewed_by_Codex'
    assert manifest['split'] == 'tuning'
