#!/usr/bin/env python3
"""Queue a bounded, backed-up repair batch for collapsed speaker diarization.

Candidates come from report-diarization-quality.py. By default this tool only
selects recordings with at least two known people, no trusted manual speaker
mappings, and either fewer detected speakers than known people or at least 90%
of labeled speech assigned to one speaker. Raw audio is never modified.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB = Path.home() / "Library/Application Support/plume-desktop/data/quill.db"
DEFAULT_AUDIT = (
    Path.home()
    / "Documents/Voice Notes Archive/20-quill-processing/diarization-collapse-audit-20260904.tsv"
)
DEFAULT_RUNS_DIR = Path.home() / "Documents/Voice Notes Archive/20-quill-processing/retranscription-runs"

BASE_PARAMS: dict[str, Any] = {
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
    "diarize_model": "nvidia_sortformer",
}


def trusted_mapping_count(conn: sqlite3.Connection, job_id: str) -> int:
    return int(
        conn.execute(
            """
            SELECT COUNT(*) FROM speaker_mappings
            WHERE transcription_job_id = ?
              AND (
                match_source = 'manual'
                OR review_status IN ('accepted', 'corrected')
                OR (
                  COALESCE(match_source, '') = ''
                  AND COALESCE(custom_name, '') != COALESCE(original_speaker, '')
                )
              )
            """,
            (job_id,),
        ).fetchone()[0]
    )


def select_candidates(
    audit_path: Path,
    conn: sqlite3.Connection,
    limit: int,
    max_hours: float,
) -> list[dict[str, Any]]:
    with audit_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    eligible: list[dict[str, Any]] = []
    for row in rows:
        known_people_count = int(row.get("known_people_count") or 0)
        speaker_count = int(row.get("speaker_count") or 0)
        dominant_ratio = float(row.get("dominant_ratio") or 0.0)
        if known_people_count < 2 or known_people_count > 4:
            continue
        if speaker_count >= known_people_count and dominant_ratio < 0.90:
            continue
        if trusted_mapping_count(conn, row["job_id"]) > 0:
            continue
        job = conn.execute(
            """
            SELECT id, title, status, model_family, model, transcript_json_path,
                   transcript_markdown_path, audio_path, artifact_dir
            FROM transcription_jobs
            WHERE id = ? AND status = 'completed' AND deleted_at IS NULL
            """,
            (row["job_id"],),
        ).fetchone()
        if job is None:
            continue
        candidate = {**row, **dict(job)}
        candidate["known_people_count"] = known_people_count
        candidate["speaker_count"] = speaker_count
        candidate["dominant_ratio"] = dominant_ratio
        candidate["duration_minutes"] = float(row.get("duration_minutes") or 0.0)
        eligible.append(candidate)

    # Exact one-speaker collapses are strongest evidence; within each evidence
    # tier, shorter recordings restore more files per batch and simplify review.
    eligible.sort(
        key=lambda row: (
            0 if row["speaker_count"] == 1 else 1,
            row["duration_minutes"],
            -int(row.get("suspicion_score") or 0),
        )
    )

    selected: list[dict[str, Any]] = []
    total_hours = 0.0
    for candidate in eligible:
        hours = candidate["duration_minutes"] / 60
        if len(selected) >= limit:
            break
        if selected and max_hours > 0 and total_hours + hours > max_hours:
            continue
        selected.append(candidate)
        total_hours += hours
    return selected


def copy_if_exists(value: str | None, destination: Path) -> str | None:
    if not value:
        return None
    source = Path(value)
    if not source.is_file():
        return None
    target = destination / source.name
    shutil.copy2(source, target)
    return str(target)


def backup_job(conn: sqlite3.Connection, run_dir: Path, job: dict[str, Any]) -> dict[str, Any]:
    job_dir = run_dir / str(job["job_id"])
    job_dir.mkdir(parents=True, exist_ok=True)
    mappings = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM speaker_mappings WHERE transcription_job_id = ? ORDER BY original_speaker",
            (job["job_id"],),
        ).fetchall()
    ]
    mappings_path = job_dir / "speaker_mappings.before.json"
    mappings_path.write_text(json.dumps(mappings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "transcript_json_backup": copy_if_exists(job.get("transcript_json_path"), job_dir),
        "transcript_markdown_backup": copy_if_exists(job.get("transcript_markdown_path"), job_dir),
        "speaker_mappings_backup": str(mappings_path),
    }


def post_start(api_base: str, job_id: str, params: dict[str, Any]) -> tuple[bool, str]:
    request = urllib.request.Request(
        f"{api_base.rstrip('/')}/api/v1/transcription/{job_id}/start",
        data=json.dumps(params).encode("utf-8"),
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--run-name", default=datetime.now().strftime("%Y%m%d-%H%M%S-diarization-repair"))
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--max-hours", type=float, default=2.0)
    parser.add_argument("--min-free-gb", type=float, default=12.0)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    db_path = args.db.expanduser().resolve()
    audit_path = args.audit.expanduser().resolve()
    run_dir = args.runs_dir.expanduser().resolve() / args.run_name
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    selected = select_candidates(audit_path, conn, args.limit, args.max_hours)
    if not selected:
        print("No safe diarization-repair candidates selected.", file=sys.stderr)
        return 1

    run_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "kind": "diarization_collapse_repair",
        "db": str(db_path),
        "audit": str(audit_path),
        "api_base": args.api_base,
        "apply": args.apply,
        "selection": {
            "limit": args.limit,
            "max_hours": args.max_hours,
            "min_free_gb": args.min_free_gb,
            "selected_count": len(selected),
            "selected_audio_hours": round(sum(row["duration_minutes"] for row in selected) / 60, 3),
        },
        "jobs": [],
    }

    for index, candidate in enumerate(selected, start=1):
        free_gb = shutil.disk_usage(db_path.parent).free / (1024**3)
        params = {**BASE_PARAMS, "max_speakers": candidate["known_people_count"]}
        entry = dict(candidate)
        entry.update({"index": index, "expected_speakers": candidate["known_people_count"], "params": params})
        entry["backup"] = backup_job(conn, run_dir, candidate) if args.apply else {}
        if args.apply and free_gb >= args.min_free_gb:
            ok, response = post_start(args.api_base, candidate["job_id"], params)
            entry.update({"queued": ok, "response": response[:1000], "free_gb_at_queue": round(free_gb, 2)})
            time.sleep(0.2)
        else:
            entry.update(
                {
                    "queued": False,
                    "response": "dry-run" if not args.apply else "stopped_low_disk",
                    "free_gb_at_queue": round(free_gb, 2),
                }
            )
        manifest["jobs"].append(entry)
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if args.apply and free_gb < args.min_free_gb:
            break

    conn.close()
    queued = sum(1 for job in manifest["jobs"] if job["queued"])
    print(f"run_dir\t{run_dir}")
    print(f"selected\t{len(selected)}")
    print(f"selected_audio_hours\t{manifest['selection']['selected_audio_hours']}")
    print(f"queued\t{queued}")
    for job in manifest["jobs"]:
        print(
            f"{job['index']}\t{job['queued']}\t{job['speaker_count']}->{job['expected_speakers']}"
            f"\t{job['duration_minutes']}m\t{job['job_id']}\t{job['title']}"
        )
    return 0 if (not args.apply or queued == len(selected)) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
