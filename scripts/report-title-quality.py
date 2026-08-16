#!/usr/bin/env python3
"""Report Plume recording titles that likely need cleanup.

This is a queueing helper, not a mutating repair tool. It classifies common bad
archive-title states so they can be fixed in the right order:

- raw_apple_filename: needs transcription/auto-title, not manual polish
- source_part_title: should usually be hidden or merged into a parent
- generic_title: likely needs retitling from transcript/summary
- location_only: preserve as @location but generate a subject
- thank_you_title: likely hallucinated; retranscribe before retitling
- missing_duration: title format is incomplete
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path


DEFAULT_DB = Path.home() / "Library/Application Support/plume-desktop/data/quill.db"

RAW_APPLE_FILENAME_RE = re.compile(r"^\d{6} \d{2}:\d{2} 20\d{6} \d{6}-[0-9A-Fa-f]{8,}$")
SOURCE_PART_RE = re.compile(r"^\d{6} \d{2}:\d{2} part \d{2} of \d{2} \(", re.IGNORECASE)
DATE_PREFIX_RE = re.compile(r"^\d{6} \d{2}:\d{2}\s+")
DURATION_RE = re.compile(r"\{\d{1,2}:?\d{2}(?::\d{2})?\}\s*$")
GENERIC_TERMS = (
    "brief note",
    "new recording",
    "recording",
    "conversation",
    "chat",
    "discussion",
    "personal issue",
    "personal update",
)
LOCATION_PREFIXES = (
    "rua ",
    "malplaquetstraße",
    "neue aufnahme",
    "nueva grabación",
)


def classify(title: str, status: str, is_source_part: int) -> list[str]:
    labels: list[str] = []
    stripped = (title or "").strip()
    lower = stripped.lower()

    if RAW_APPLE_FILENAME_RE.match(stripped):
        labels.append("raw_apple_filename")
    if is_source_part or SOURCE_PART_RE.match(stripped):
        labels.append("source_part_title")
    if "thank you" in lower:
        labels.append("thank_you_title")
    if not DURATION_RE.search(stripped) and status == "completed":
        labels.append("missing_duration")

    subject = DATE_PREFIX_RE.sub("", stripped).strip()
    subject_without_duration = DURATION_RE.sub("", subject).strip()
    subject_lower = subject_without_duration.lower()

    if subject_lower in GENERIC_TERMS or any(subject_lower.startswith(term + " ") for term in GENERIC_TERMS):
        labels.append("generic_title")
    if any(subject_lower.startswith(prefix) for prefix in LOCATION_PREFIXES):
        labels.append("location_only")
    if status == "uploaded" and (RAW_APPLE_FILENAME_RE.match(stripped) or labels == ["location_only"]):
        labels.append("needs_transcription")

    return labels


def load_rows(conn: sqlite3.Connection, include_source_parts: bool) -> list[sqlite3.Row]:
    source_clause = "" if include_source_parts else "AND (is_source_part = 0 OR is_source_part IS NULL)"
    return list(
        conn.execute(
            f"""
            SELECT id, title, status, recorded_at, created_at, model, model_family,
                   is_source_part, source_part_count, original_filename, audio_path
            FROM transcription_jobs
            WHERE deleted_at IS NULL
              {source_clause}
            ORDER BY recorded_at DESC, created_at DESC
            """
        )
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to Plume quill.db")
    parser.add_argument("--include-source-parts", action="store_true", help="Include split chunk/source-part rows")
    parser.add_argument("--limit", type=int, default=200, help="Maximum issue rows to print")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(Path(args.db).expanduser())
    conn.row_factory = sqlite3.Row
    rows = load_rows(conn, args.include_source_parts)

    issues = []
    counts: dict[str, int] = {}
    for row in rows:
        labels = classify(row["title"] or "", row["status"] or "", int(row["is_source_part"] or 0))
        if not labels:
            continue
        for label in labels:
            counts[label] = counts.get(label, 0) + 1
        issues.append((labels, row))

    for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"# {label}: {count}")
    if counts:
        print()

    writer = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
    writer.writerow(
        [
            "labels",
            "status",
            "recorded_at",
            "model_family",
            "model",
            "source_part_count",
            "job_id",
            "title",
            "audio_path",
        ]
    )
    for labels, row in issues[: args.limit]:
        writer.writerow(
            [
                ",".join(labels),
                row["status"] or "",
                row["recorded_at"] or row["created_at"] or "",
                row["model_family"] or "",
                row["model"] or "",
                row["source_part_count"] or "",
                row["id"],
                row["title"] or "",
                row["audio_path"] or "",
            ]
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
