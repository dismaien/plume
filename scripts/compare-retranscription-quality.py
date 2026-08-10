#!/usr/bin/env python3
"""Compare transcript quality before/after a Plume retranscription run.

The queue helper stores each selected job plus a backup of the previous
transcript. This report reads that manifest, compares the backed-up transcript
with the current DB transcript, and writes a TSV plus JSON summary. The score is
intentionally narrow: it detects catastrophic repetition hallucinations, not
semantic transcript accuracy.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_DB = Path.home() / "Library/Application Support/plume-desktop/data/quill.db"
DEFAULT_RUN = (
    Path.home()
    / "Documents/Voice Notes Archive/20-quill-processing/retranscription-runs/20260810-whisperx-quality-batch-01"
)


@dataclass
class Metrics:
    score: int
    thank_you: int
    big_big: int
    repeated_runs: int
    word_count: int
    unique_word_ratio: float


@dataclass
class ComparisonRow:
    job_id: str
    status: str
    old_model_family: str
    old_model: str
    new_model_family: str
    new_model: str
    title: str
    before_score: int
    after_score: int
    score_delta: int
    before_thank_you: int
    after_thank_you: int
    before_big_big: int
    after_big_big: int
    before_repeated_runs: int
    after_repeated_runs: int
    before_word_count: int
    after_word_count: int
    after_unique_word_ratio: float
    verdict: str
    before_transcript_path: str
    after_transcript_path: str


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
    try:
        data = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return ""

    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return ""

    segments = data.get("segments") or []
    text = " ".join(
        str(segment.get("text") or "")
        for segment in segments
        if isinstance(segment, dict)
    ).strip()
    if text:
        return text
    return str(data.get("text") or "")


def metrics_for(path: str | Path | None) -> Metrics:
    if not path:
        return Metrics(0, 0, 0, 0, 0, 0.0)

    text = transcript_text(Path(path))
    lower = text.lower()
    words = words_from(text)
    thank_you = len(re.findall(r"\bthank you\b", lower))
    big_big = len(re.findall(r"\bbig big\b", lower))
    repeated_runs = repeated_word_runs(words)
    score = thank_you * 2 + big_big * 3 + repeated_runs * 10
    unique_word_ratio = round(len(set(words)) / len(words), 4) if words else 0.0
    return Metrics(score, thank_you, big_big, repeated_runs, len(words), unique_word_ratio)


def load_manifest(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text())


def load_current_jobs(db_path: Path, job_ids: list[str]) -> dict[str, sqlite3.Row]:
    if not job_ids:
        return {}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in job_ids)
    rows = conn.execute(
        f"""
        SELECT id, title, status, model_family, model, transcript_json_path
        FROM transcription_jobs
        WHERE id IN ({placeholders})
        """,
        job_ids,
    ).fetchall()
    conn.close()
    return {row["id"]: row for row in rows}


def verdict(status: str, before: Metrics, after: Metrics, threshold: int) -> str:
    if status not in {"completed", "failed"}:
        return "waiting"
    if status == "failed":
        return "failed"
    if after.score < threshold:
        return "passed_repetition_gate"
    if after.score < before.score:
        return "improved_but_review"
    if after.score == before.score:
        return "unchanged_review"
    return "worse_review"


def compare(run_dir: Path, db_path: Path, threshold: int) -> tuple[list[ComparisonRow], dict[str, Any]]:
    manifest = load_manifest(run_dir)
    jobs = manifest.get("jobs") or []
    job_ids = [str(job.get("job_id") or "") for job in jobs if job.get("job_id")]
    current_by_id = load_current_jobs(db_path, job_ids)

    rows: list[ComparisonRow] = []
    for job in jobs:
        job_id = str(job.get("job_id") or "")
        current = current_by_id.get(job_id)
        backup = job.get("backup") or {}
        before_path = backup.get("transcript_json_backup") or job.get("transcript_json_path") or ""
        after_path = current["transcript_json_path"] if current else ""

        before = metrics_for(before_path)
        after = metrics_for(after_path)
        status = str(current["status"] if current else "missing")

        rows.append(
            ComparisonRow(
                job_id=job_id,
                status=status,
                old_model_family=str(job.get("model_family") or ""),
                old_model=str(job.get("model") or ""),
                new_model_family=str(current["model_family"] if current else ""),
                new_model=str(current["model"] if current else ""),
                title=str(current["title"] if current else job.get("title") or ""),
                before_score=before.score,
                after_score=after.score,
                score_delta=after.score - before.score,
                before_thank_you=before.thank_you,
                after_thank_you=after.thank_you,
                before_big_big=before.big_big,
                after_big_big=after.big_big,
                before_repeated_runs=before.repeated_runs,
                after_repeated_runs=after.repeated_runs,
                before_word_count=before.word_count,
                after_word_count=after.word_count,
                after_unique_word_ratio=after.unique_word_ratio,
                verdict=verdict(status, before, after, threshold),
                before_transcript_path=str(before_path),
                after_transcript_path=str(after_path),
            )
        )

    summary: dict[str, Any] = {
        "run_dir": str(run_dir),
        "db": str(db_path),
        "threshold": threshold,
        "total": len(rows),
        "by_status": {},
        "by_verdict": {},
        "completed": 0,
        "completed_score_delta": 0,
    }
    for row in rows:
        summary["by_status"][row.status] = summary["by_status"].get(row.status, 0) + 1
        summary["by_verdict"][row.verdict] = summary["by_verdict"].get(row.verdict, 0) + 1
        if row.status == "completed":
            summary["completed"] += 1
            summary["completed_score_delta"] += row.score_delta
    if summary["completed"]:
        summary["average_completed_score_delta"] = round(
            summary["completed_score_delta"] / summary["completed"],
            2,
        )

    return rows, summary


def write_reports(run_dir: Path, rows: list[ComparisonRow], summary: dict[str, Any]) -> tuple[Path, Path]:
    tsv_path = run_dir / "quality-comparison.tsv"
    json_path = run_dir / "quality-comparison-summary.json"

    with tsv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(ComparisonRow.__dataclass_fields__.keys()), delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))

    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return tsv_path, json_path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--threshold", type=int, default=12)
    args = parser.parse_args(argv)

    rows, summary = compare(args.run_dir.expanduser(), args.db.expanduser(), args.threshold)
    tsv_path, json_path = write_reports(args.run_dir.expanduser(), rows, summary)

    print(json.dumps({**summary, "tsv_path": str(tsv_path), "json_path": str(json_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
