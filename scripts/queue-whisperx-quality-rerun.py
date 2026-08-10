#!/usr/bin/env python3
"""Queue high-quality WhisperX re-transcription for bad Plume transcripts.

This tool is intentionally non-destructive to raw audio. Before each job is
queued it copies the current transcript JSON/Markdown and speaker mappings into
a run manifest directory, then calls Plume's single-job /start endpoint so stale
speaker mappings are cleared correctly.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB = Path.home() / "Library/Application Support/plume-desktop/data/quill.db"
DEFAULT_RUNS_DIR = Path.home() / "Documents/Voice Notes Archive/20-quill-processing/retranscription-runs"
DEFAULT_API_BASE = "http://127.0.0.1:59152"

WHISPERX_PARAMS: dict[str, Any] = {
    "model_family": "whisper",
    "model": "large-v3",
    "device": "cpu",
    "batch_size": 8,
    "compute_type": "int8",
    "vad_preprocess": False,
    "vad_method": "silero",
    "vad_onset": 0.5,
    "vad_offset": 0.363,
    "chunk_size": 30,
    "temperature": 0,
    "best_of": 5,
    "beam_size": 5,
    "condition_on_previous_text": False,
    "compression_ratio_threshold": 2.4,
    "logprob_threshold": -1.0,
    "no_speech_threshold": 0.6,
    "diarize": True,
    "diarize_model": "sherpa-onnx",
}


@dataclass
class Candidate:
    score: int
    thank_you: int
    big_big: int
    repeated_runs: int
    word_count: int
    duration_seconds: float
    model_family: str
    model: str
    job_id: str
    title: str
    transcript_json_path: str
    transcript_markdown_path: str
    audio_path: str
    artifact_dir: str


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


def read_transcript(path: Path) -> tuple[str, float]:
    data = json.loads(path.read_text(errors="replace"))
    segments = data.get("segments") or []
    texts: list[str] = []
    duration = 0.0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = segment.get("text")
        if text:
            texts.append(str(text))
        end = segment.get("end")
        if isinstance(end, (int, float)):
            duration = max(duration, float(end))
    return " ".join(texts), duration


def scan_candidates(db_path: Path, min_score: int, include_large_v3: bool) -> list[Candidate]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, title, model_family, model, transcript_json_path,
               transcript_markdown_path, audio_path, artifact_dir
        FROM transcription_jobs
        WHERE status = 'completed'
          AND deleted_at IS NULL
          AND (is_source_part = 0 OR is_source_part IS NULL)
          AND transcript_json_path IS NOT NULL
          AND transcript_json_path != ''
        """
    ).fetchall()

    out: list[Candidate] = []
    for row in rows:
        model_family = row["model_family"] or ""
        model = row["model"] or ""
        if not include_large_v3 and model_family == "whisper" and model == "large-v3":
            continue

        path = Path(row["transcript_json_path"])
        if not path.exists():
            continue
        try:
            text, duration = read_transcript(path)
        except (OSError, json.JSONDecodeError):
            continue

        lower = text.lower()
        words = words_from(text)
        thank_you = len(re.findall(r"\bthank you\b", lower))
        big_big = len(re.findall(r"\bbig big\b", lower))
        repeated_runs = repeated_word_runs(words)
        score = thank_you * 2 + big_big * 3 + repeated_runs * 10
        if score < min_score:
            continue

        out.append(
            Candidate(
                score=score,
                thank_you=thank_you,
                big_big=big_big,
                repeated_runs=repeated_runs,
                word_count=len(words),
                duration_seconds=duration,
                model_family=model_family,
                model=model,
                job_id=row["id"],
                title=row["title"] or "",
                transcript_json_path=row["transcript_json_path"] or "",
                transcript_markdown_path=row["transcript_markdown_path"] or "",
                audio_path=row["audio_path"] or "",
                artifact_dir=row["artifact_dir"] or "",
            )
        )

    out.sort(key=lambda c: c.score, reverse=True)
    return out


def total_hours(candidates: list[Candidate]) -> float:
    return sum(c.duration_seconds for c in candidates) / 3600


def select_batch(candidates: list[Candidate], limit: int, max_hours: float) -> list[Candidate]:
    selected: list[Candidate] = []
    for candidate in candidates:
        if len(selected) >= limit:
            break
        next_hours = total_hours(selected) + candidate.duration_seconds / 3600
        if selected and max_hours > 0 and next_hours > max_hours:
            continue
        selected.append(candidate)
    return selected


def copy_if_exists(src: str, dst_dir: Path) -> str | None:
    if not src:
        return None
    path = Path(src)
    if not path.exists() or not path.is_file():
        return None
    dst = dst_dir / path.name
    shutil.copy2(path, dst)
    return str(dst)


def backup_job(conn: sqlite3.Connection, run_dir: Path, candidate: Candidate) -> dict[str, Any]:
    job_dir = run_dir / candidate.job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    mapping_rows = conn.execute(
        "SELECT * FROM speaker_mappings WHERE transcription_job_id = ? ORDER BY original_speaker",
        (candidate.job_id,),
    ).fetchall()
    mappings_path = job_dir / "speaker_mappings.before.json"
    mappings_path.write_text(
        json.dumps([dict(row) for row in mapping_rows], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "transcript_json_backup": copy_if_exists(candidate.transcript_json_path, job_dir),
        "transcript_markdown_backup": copy_if_exists(candidate.transcript_markdown_path, job_dir),
        "speaker_mappings_backup": str(mappings_path),
    }


def post_start(api_base: str, job_id: str) -> tuple[bool, str]:
    url = f"{api_base.rstrip('/')}/api/v1/transcription/{job_id}/start"
    body = json.dumps(WHISPERX_PARAMS).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return 200 <= response.status < 300, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return False, error.read().decode("utf-8", errors="replace") or str(error)
    except OSError as error:
        return False, str(error)


def write_tsv(run_dir: Path, candidates: list[Candidate]) -> None:
    with (run_dir / "selected.tsv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(candidates[0]).keys()), delimiter="\t")
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(asdict(candidate))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to Plume quill.db")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    parser.add_argument("--run-name", default=datetime.now().strftime("%Y%m%d-whisperx-quality-batch"))
    parser.add_argument("--min-score", type=int, default=250)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--max-hours", type=float, default=60.0)
    parser.add_argument("--include-large-v3", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    db_path = Path(args.db).expanduser()
    run_dir = Path(args.runs_dir).expanduser() / args.run_name
    candidates = scan_candidates(db_path, args.min_score, args.include_large_v3)
    selected = select_batch(candidates, args.limit, args.max_hours)
    if not selected:
        print("No candidates selected.", file=sys.stderr)
        return 1

    run_dir.mkdir(parents=True, exist_ok=True)
    write_tsv(run_dir, selected)

    manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "api_base": args.api_base,
        "apply": args.apply,
        "params": WHISPERX_PARAMS,
        "selection": {
            "min_score": args.min_score,
            "limit": args.limit,
            "max_hours": args.max_hours,
            "include_large_v3": args.include_large_v3,
            "selected_count": len(selected),
            "selected_audio_hours": round(total_hours(selected), 3),
        },
        "jobs": [],
    }

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    for index, candidate in enumerate(selected, start=1):
        entry = asdict(candidate)
        entry["index"] = index
        entry["backup"] = backup_job(conn, run_dir, candidate) if args.apply else {}
        if args.apply:
            ok, response = post_start(args.api_base, candidate.job_id)
            entry["queued"] = ok
            entry["response"] = response[:1000]
            time.sleep(0.2)
        else:
            entry["queued"] = False
            entry["response"] = "dry-run"
        manifest["jobs"].append(entry)

    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    queued = sum(1 for job in manifest["jobs"] if job["queued"])
    print(f"run_dir\t{run_dir}")
    print(f"selected\t{len(selected)}")
    print(f"selected_audio_hours\t{manifest['selection']['selected_audio_hours']}")
    print(f"queued\t{queued}")
    for job in manifest["jobs"]:
        print(f"{job['index']}\t{job['queued']}\t{job['score']}\t{job['job_id']}\t{job['title']}")

    return 0 if (queued == len(selected) if args.apply else True) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
