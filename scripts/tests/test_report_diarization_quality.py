import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "report-diarization-quality.py"
SPEC = importlib.util.spec_from_file_location("report_diarization_quality", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_flags_extra_labels_for_trusted_people(tmp_path):
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 0, "end": 100, "speaker": "speaker_0"},
                    {"start": 100, "end": 200, "speaker": "speaker_1"},
                    {"start": 200, "end": 300, "speaker": "speaker_2"},
                ]
            }
        ),
        encoding="utf-8",
    )
    db = tmp_path / "quill.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE transcription_jobs (
            id TEXT, title TEXT, model TEXT, diarize_model TEXT,
            transcript_json_path TEXT, audio_path TEXT, status TEXT,
            deleted_at TEXT, is_source_part INTEGER
        );
        CREATE TABLE speaker_mappings (
            transcription_job_id TEXT, original_speaker TEXT,
            custom_name TEXT, match_source TEXT, review_status TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO transcription_jobs VALUES (?, ?, ?, ?, ?, ?, 'completed', NULL, 0)",
        ("job-1", "Conversation [Leona, Mishka]", "large-v3", "nvidia_sortformer", str(transcript), "audio.m4a"),
    )
    conn.commit()
    conn.close()

    rows = MODULE.scan(db)

    assert len(rows) == 1
    assert rows[0].reason == "extra_speaker_labels"
    assert rows[0].speaker_count == 3
    assert rows[0].known_people_count == 2
