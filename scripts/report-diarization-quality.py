#!/usr/bin/env python3
"""Rank completed Plume recordings whose speaker diarization likely collapsed.

This is a conservative audit helper. It does not modify transcripts. A result
is included when trusted names imply multiple people but the transcript does
not, or when sherpa-onnx emitted several labels while assigning nearly all
speech to one label with very few turns.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_DB = Path.home() / "Library/Application Support/plume-desktop/data/quill.db"


@dataclass
class DiarizationRow:
    suspicion_score: int
    reason: str
    job_id: str
    title: str
    duration_minutes: float
    model: str
    diarize_model: str
    speaker_count: int
    dominant_speaker: str
    dominant_ratio: float
    switches: int
    switches_per_hour: float
    known_people_count: int
    known_people: str
    transcript_json_path: str
    audio_path: str


def title_people(title: str) -> set[str]:
    people: set[str] = set()
    for contents in re.findall(r"\[([^\]]+)\]", title):
        for name in contents.split(","):
            cleaned = name.strip()
            if cleaned and not re.fullmatch(r"speaker\s+[a-z0-9]+", cleaned, re.I):
                people.add(cleaned)
    return people


def trusted_people(conn: sqlite3.Connection) -> dict[str, set[str]]:
    rows = conn.execute(
        """
        SELECT transcription_job_id, original_speaker, custom_name,
               match_source, review_status
        FROM speaker_mappings
        """
    ).fetchall()
    result: dict[str, set[str]] = {}
    for row in rows:
        name = str(row["custom_name"] or "").strip()
        original = str(row["original_speaker"] or "").strip()
        source = str(row["match_source"] or "").strip()
        review = str(row["review_status"] or "").strip()
        trusted = source == "manual" or review in {"accepted", "corrected"}
        trusted = trusted or (not source and name and name != original)
        if trusted and name:
            result.setdefault(str(row["transcription_job_id"]), set()).add(name)
    return result


def transcript_metrics(path: Path) -> tuple[float, Counter[str], int]:
    payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    segments = payload.get("segments") or []
    durations: Counter[str] = Counter()
    duration = 0.0
    switches = 0
    previous = ""
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        duration = max(duration, end)
        speaker = str(segment.get("speaker") or "").strip()
        if not speaker:
            continue
        durations[speaker] += max(0.0, end - start)
        if previous and previous != speaker:
            switches += 1
        previous = speaker
    return duration, durations, switches


def scan(db_path: Path) -> list[DiarizationRow]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    people_by_job = trusted_people(conn)
    jobs = conn.execute(
        """
        SELECT id, title, model, diarize_model, transcript_json_path, audio_path
        FROM transcription_jobs
        WHERE status = 'completed'
          AND deleted_at IS NULL
          AND (is_source_part = 0 OR is_source_part IS NULL)
          AND transcript_json_path IS NOT NULL
          AND transcript_json_path != ''
        """
    ).fetchall()

    results: list[DiarizationRow] = []
    for job in jobs:
        path = Path(str(job["transcript_json_path"]))
        if not path.exists():
            continue
        try:
            duration, speaker_durations, switches = transcript_metrics(path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if duration < 120:
            continue

        total_speech = sum(speaker_durations.values())
        speaker_count = len(speaker_durations)
        dominant_speaker, dominant_seconds = (
            speaker_durations.most_common(1)[0] if speaker_durations else ("", 0.0)
        )
        dominant_ratio = dominant_seconds / total_speech if total_speech else 1.0
        switches_per_hour = switches / (duration / 3600) if duration else 0.0
        known = set(people_by_job.get(str(job["id"]), set()))
        known.update(title_people(str(job["title"] or "")))

        reasons: list[str] = []
        if len(known) >= 2 and speaker_count < len(known):
            reasons.append("known_people_missing")
        if len(known) >= 2 and dominant_ratio >= 0.90:
            reasons.append("known_conversation_dominated")
        if (
            str(job["diarize_model"] or "") in {"sherpa-onnx", "sherpa_onnx"}
            and duration >= 300
            and speaker_count >= 2
            and dominant_ratio >= 0.95
            and switches_per_hour < 20
        ):
            reasons.append("sherpa_collapse_signature")
        if not reasons:
            continue

        score = round(dominant_ratio * 100)
        score += max(0, 20 - round(switches_per_hour)) * 2
        score += 100 if len(known) >= 2 else 0
        score += 50 if speaker_count < len(known) else 0
        results.append(
            DiarizationRow(
                suspicion_score=score,
                reason=",".join(reasons),
                job_id=str(job["id"]),
                title=str(job["title"] or ""),
                duration_minutes=round(duration / 60, 2),
                model=str(job["model"] or ""),
                diarize_model=str(job["diarize_model"] or ""),
                speaker_count=speaker_count,
                dominant_speaker=dominant_speaker,
                dominant_ratio=round(dominant_ratio, 4),
                switches=switches,
                switches_per_hour=round(switches_per_hour, 2),
                known_people_count=len(known),
                known_people=", ".join(sorted(known)),
                transcript_json_path=str(path),
                audio_path=str(job["audio_path"] or ""),
            )
        )

    conn.close()
    results.sort(key=lambda row: (row.suspicion_score, row.duration_minutes), reverse=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    rows = scan(args.db.expanduser())
    if args.limit > 0:
        rows = rows[: args.limit]
    fieldnames = list(DiarizationRow.__dataclass_fields__)
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, delimiter="\t")
    writer.writeheader()
    for row in rows:
        writer.writerow(asdict(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
