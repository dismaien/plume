"""Focused tests for global voice-identity reclustering."""

import importlib.util
from pathlib import Path

import numpy as np


MODULE_PATH = Path(__file__).parent.parent / "recluster_speakers.py"
SPEC = importlib.util.spec_from_file_location("recluster_speakers", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def turn(start, speaker):
    return {"start": start, "end": start + 4.0, "speaker": speaker}


def test_recluster_merges_multiple_heads_for_same_voice():
    turns = [
        turn(0, "speaker_0"),
        turn(5, "speaker_1"),
        turn(10, "speaker_2"),
        turn(15, "speaker_1"),
        turn(20, "speaker_3"),
        turn(25, "speaker_1"),
        turn(30, "speaker_0"),
        turn(35, "speaker_1"),
        turn(40, "speaker_2"),
        turn(45, "speaker_1"),
        turn(50, "speaker_3"),
        turn(55, "speaker_1"),
    ]
    # Heads 0, 2 and 3 are one voice; head 1 is the other voice.
    voice_a = np.array([1.0, 0.02, 0.0, 0.0])
    voice_b = np.array([0.0, 1.0, 0.02, 0.0])
    embeddings = np.vstack([
        voice_a, voice_b, voice_a, voice_b, voice_a, voice_b,
        voice_a, voice_b, voice_a, voice_b, voice_a, voice_b,
    ])

    result = MODULE.recluster(turns, turns, embeddings)

    assert result["status"] == "accepted"
    assert result["identity_count"] == 2
    by_head = {}
    for segment in result["segments"]:
        original = next(t for t in turns if t["start"] == segment["start"])
        by_head.setdefault(original["speaker"], set()).add(segment["speaker"])
    assert by_head["speaker_0"] == by_head["speaker_2"] == by_head["speaker_3"]
    assert by_head["speaker_1"] != by_head["speaker_0"]


def test_recluster_fails_closed_for_weakly_separated_voices():
    turns = [turn(index * 5, f"speaker_{index % 4}") for index in range(12)]
    # Deliberately overlapping clouds with no defensible global identities.
    embeddings = np.array([
        [1.00, 0.00, 0.00], [0.99, 0.02, 0.00], [0.98, 0.04, 0.00],
        [0.97, 0.06, 0.00], [0.96, 0.08, 0.00], [0.95, 0.10, 0.00],
        [0.94, 0.12, 0.00], [0.93, 0.14, 0.00], [0.92, 0.16, 0.00],
        [0.91, 0.18, 0.00], [0.90, 0.20, 0.00], [0.89, 0.22, 0.00],
    ])

    result = MODULE.recluster(turns, turns, embeddings)

    assert result["status"] == "uncertain"
    assert len(result["segments"]) == len(turns)
    assert all(s["speaker"] == "" for s in result["segments"])


def test_distinct_brief_speaker_is_not_erased_by_duration_share():
    turns = [{"start": i * 20., "end": (i + 1) * 20., "speaker": "head0"} for i in range(16)]
    turns += [{"start": 330. + i*4, "end": 334. + i*4, "speaker": "head1"} for i in range(3)]
    vectors = np.array([[1., 0.]]*16 + [[0., 1.]]*3)
    result = MODULE.recluster(turns, turns, vectors)
    assert result["identity_count"] == 2
    assert len(result["segments"]) == len(turns)
    assert len({t["speaker"] for t in result["segments"]}) == 2
