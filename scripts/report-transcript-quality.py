#!/usr/bin/env python3
"""Report transcripts that look like Whisper repetition hallucinations.

This is a conservative queueing helper, not a deletion or cleanup tool. It reads
the Plume SQLite database and transcript JSON files, then ranks completed parent
recordings by repeated filler phrases and long repeated-word runs.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_DB = Path.home() / "Library/Application Support/plume-desktop/data/quill.db"


@dataclass
class QualityRow:
    score: int
    thank_you: int
    big_big: int
    repeated_runs: int
    word_count: int
    model: str
    job_id: str
    title: str
    transcript_json_path: str
    audio_path: str


def words_from(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z']+", text.lower())


def repeated_word_runs(words: list[str], *, min_run: int = 8) -> int:
    if not words:
        return 0

    runs = 0
    current = 1
    previous = words[0]

    for word in words[1:]:
        if word == previous:
            current += 1
            continue
        if current >= min_run:
            runs += 1
        previous = word
        current = 1

    if current >= min_run:
        runs += 1
    return runs


def transcript_text(path: Path) -> str:
    data = json.loads(path.read_text(errors="replace"))
    segments = data.get("segments") or []
    return " ".join(
        segment.get("text") or ""
        for segment in segments
        if isinstance(segment, dict)
    )


def scan(db_path: Path) -> Iterable[QualityRow]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, title, model, transcript_json_path, audio_path
        FROM transcription_jobs
        WHERE status = 'completed'
          AND (is_source_part = 0 OR is_source_part IS NULL)
          AND transcript_json_path IS NOT NULL
          AND transcript_json_path != ''
        """
    ).fetchall()

    for row in rows:
        path = Path(row["transcript_json_path"])
        if not path.exists():
            continue

        try:
            text = transcript_text(path)
        except (OSError, json.JSONDecodeError):
            continue

        lower = text.lower()
        words = words_from(text)
        thank_you = len(re.findall(r"\bthank you\b", lower))
        big_big = len(re.findall(r"\bbig big\b", lower))
        repeated_runs = repeated_word_runs(words)
        score = thank_you * 2 + big_big * 3 + repeated_runs * 10

        if score <= 0:
            continue

        yield QualityRow(
            score=score,
            thank_you=thank_you,
            big_big=big_big,
            repeated_runs=repeated_runs,
            word_count=len(words),
            model=row["model"] or "",
            job_id=row["id"],
            title=row["title"] or "",
            transcript_json_path=row["transcript_json_path"] or "",
            audio_path=row["audio_path"] or "",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--min-score", type=int, default=12)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    rows = [row for row in scan(args.db) if row.score >= args.min_score]
    rows.sort(key=lambda row: row.score, reverse=True)
    if args.limit > 0:
        rows = rows[: args.limit]

    fieldnames = [
        "score",
        "thank_you",
        "big_big",
        "repeated_runs",
        "word_count",
        "model",
        "job_id",
        "title",
        "transcript_json_path",
        "audio_path",
    ]
    writer = csv.DictWriter(__import__("sys").stdout, fieldnames=fieldnames, delimiter="\t")
    writer.writeheader()
    for row in rows:
        writer.writerow(row.__dict__)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
