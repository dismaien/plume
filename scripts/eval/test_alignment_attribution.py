from pathlib import Path
import sys

import numpy as np

from activity_timeline import save_activity

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "internal/transcription/adapters/py/nvidia"))
from alignment_attribution import build_evaluation_proposals, matched_decoder_words


def test_ordered_matching_handles_duplicate_words():
    aligned = [{"word": word} for word in ("yes", "and", "yes", "now")]
    decoded = [{"word": word} for word in ("yes", "well", "and", "yes", "now")]
    assert matched_decoder_words(aligned, decoded) == {0: 0, 1: 2, 2: 3, 3: 4}


def test_trailing_activity_island_is_a_review_only_hypothesis(tmp_path):
    primary = {"regular_segments": [{"start": 0, "end": 1.5, "speaker": "speaker_00"}]}
    native = {"regular_segments": [
        {"start": 0, "end": .25, "speaker": "speaker_0"},
        {"start": .65, "end": .80, "speaker": "speaker_1"},
    ]}
    logits = np.full((150, 2), -8.0)
    logits[:25, 0] = 8.0
    logits[65:80, 1] = 8.0
    native["activity_artifact"] = save_activity(tmp_path / "activity.npz", logits, np.ones(150), .01, 1.5)
    words = [
        {"start": 0, "end": 0, "word": "point", "speaker": "speaker_00"},
        {"start": 0, "end": 1.5, "word": "No.", "speaker": "speaker_00"},
    ]
    decoder = [
        {"start": 0, "end": 0, "word": "point", "probability": .99},
        {"start": 0, "end": .8, "word": " No.", "probability": .13},
    ]
    original = [dict(word) for word in words]
    result = build_evaluation_proposals(primary, native, words, decoder)
    assert words == original
    assert result["proposals"] == [{
        "index": 1, "start": 0.0, "end": 1.5, "word": "No.",
        "previous": "speaker_00", "proposed": "speaker_1",
        "activity": result["proposals"][0]["activity"],
        "margin": result["proposals"][0]["margin"],
    }]
    diagnostic = result["alignment_diagnostics"][0]
    assert diagnostic["evidence_start"] == .65
    assert diagnostic["evidence_end"] == .8
    assert diagnostic["outcome"] == "evaluation_only_identity_hypothesis"


def test_no_decoder_sidecar_preserves_known_label_policy(tmp_path):
    primary = {"regular_segments": [{"start": 0, "end": 1, "speaker": "speaker_00"}]}
    native = {"regular_segments": [{"start": 0, "end": 1, "speaker": "speaker_0"}]}
    logits = np.tile([8.0, -8.0], (100, 1))
    native["activity_artifact"] = save_activity(tmp_path / "activity.npz", logits, np.ones(100), .01, 1.0)
    words = [{"start": 0, "end": 1, "word": "hello", "speaker": "speaker_00"}]
    result = build_evaluation_proposals(primary, native, words, [])
    assert result["proposals"] == []
    assert result["alignment_diagnostics"] == []
