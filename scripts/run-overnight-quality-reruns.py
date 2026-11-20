#!/usr/bin/env python3
"""Run resumable, bounded WhisperX quality batches while Plume is open.

The controller never touches raw archive audio. It waits for Plume's queue to
be idle, queues one bounded batch, waits for completion, writes a before/after
quality report, and repeats until its deadline or free-space floor is reached.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB = Path.home() / "Library/Application Support/plume-desktop/data/quill.db"
DEFAULT_RUNS_DIR = Path.home() / "Documents/Voice Notes Archive/20-quill-processing/retranscription-runs"
DEFAULT_API_BASE = "http://127.0.0.1:59152"
DEFAULT_RETRY_JOB = "49058680-88d4-4281-b6c3-bc3dcc7ef9aa"
ACTIVE_STATUSES = ("pending", "processing")
TERMINAL_STATUSES = ("completed", "failed")
SCRIPT_DIR = Path(__file__).resolve().parent

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
    "diarize_model": "nvidia_sortformer",
    "max_speakers": 4,
}


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(handle: Any, message: str) -> None:
    line = f"{now_text()}\t{message}"
    print(line, flush=True)
    handle.write(line + "\n")
    handle.flush()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def active_count(db_path: Path) -> int:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM transcription_jobs
            WHERE deleted_at IS NULL AND status IN (?, ?)
            """,
            ACTIVE_STATUSES,
        ).fetchone()
    return int(row["count"])


def job_statuses(db_path: Path, job_ids: list[str]) -> dict[str, str]:
    if not job_ids:
        return {}
    placeholders = ",".join("?" for _ in job_ids)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT id, status FROM transcription_jobs WHERE id IN ({placeholders})",
            job_ids,
        ).fetchall()
    return {str(row["id"]): str(row["status"]) for row in rows}


def wait_for_idle(db_path: Path, poll_seconds: int, deadline: datetime, handle: Any) -> bool:
    last_count: int | None = None
    while datetime.now(timezone.utc) < deadline:
        count = active_count(db_path)
        if count == 0:
            return True
        if count != last_count:
            log(handle, f"waiting_for_idle active_jobs={count}")
            last_count = count
        time.sleep(poll_seconds)
    return False


def wait_for_jobs(
    db_path: Path,
    job_ids: list[str],
    poll_seconds: int,
    deadline: datetime,
    handle: Any,
) -> bool:
    last_summary = ""
    while datetime.now(timezone.utc) < deadline:
        statuses = job_statuses(db_path, job_ids)
        counts: dict[str, int] = {}
        for status in statuses.values():
            counts[status] = counts.get(status, 0) + 1
        summary = " ".join(f"{key}={counts[key]}" for key in sorted(counts))
        if summary != last_summary:
            log(handle, f"batch_status {summary}")
            last_summary = summary
        if len(statuses) == len(job_ids) and all(status in TERMINAL_STATUSES for status in statuses.values()):
            return True
        time.sleep(poll_seconds)
    return False


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


def retry_failed_job(
    db_path: Path,
    api_base: str,
    job_id: str,
    runs_dir: Path,
    handle: Any,
) -> Path | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, title, status, error_message FROM transcription_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    if row is None:
        log(handle, f"retry_skipped job_not_found={job_id}")
        return None
    if row["status"] != "failed":
        log(handle, f"retry_skipped job={job_id} status={row['status']}")
        return None

    run_dir = runs_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-whisperx-retry-{job_id[:8]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    params = {**WHISPERX_PARAMS, "language": "en"}
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "kind": "failed_job_retry",
        "job_id": job_id,
        "title": row["title"],
        "previous_status": row["status"],
        "previous_error": row["error_message"],
        "params": params,
        "reason": "Force English to avoid WhisperX jw alignment-model failure.",
    }
    ok, response = post_start(api_base, job_id, params)
    manifest["queued"] = ok
    manifest["response"] = response[:2000]
    (run_dir / "retry-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log(handle, f"retry_queued={ok} job={job_id} title={row['title']!r} run_dir={run_dir}")
    if not ok:
        log(handle, f"retry_error job={job_id} response={response[:500]!r}")
        return None
    return run_dir


def queue_batch(
    db_path: Path,
    api_base: str,
    runs_dir: Path,
    batch_number: int,
    min_score: int,
    limit: int,
    max_hours: float,
    handle: Any,
) -> tuple[Path | None, list[str]]:
    run_name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-whisperx-overnight-b{batch_number:02d}"
    command = [
        sys.executable,
        str(SCRIPT_DIR / "queue-whisperx-quality-rerun.py"),
        "--db",
        str(db_path),
        "--api-base",
        api_base,
        "--runs-dir",
        str(runs_dir),
        "--run-name",
        run_name,
        "--min-score",
        str(min_score),
        "--limit",
        str(limit),
        "--max-hours",
        str(max_hours),
        "--skip-human-speaker-mappings",
        "--apply",
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.stdout:
        for line in result.stdout.rstrip().splitlines():
            log(handle, f"queue {line}")
    if result.returncode != 0:
        message = result.stderr.strip() or f"exit={result.returncode}"
        log(handle, f"queue_stopped {message}")
        return None, []

    run_dir = runs_dir / run_name
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    job_ids = [str(job["job_id"]) for job in manifest["jobs"] if job.get("queued")]
    return run_dir, job_ids


def write_quality_report(run_dir: Path, db_path: Path, handle: Any) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "compare-retranscription-quality.py"),
            "--run-dir",
            str(run_dir),
            "--db",
            str(db_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        summary = json.loads(result.stdout)
        log(
            handle,
            "quality_report "
            f"run={run_dir.name} completed={summary.get('completed', 0)} "
            f"verdicts={json.dumps(summary.get('by_verdict', {}), sort_keys=True)}",
        )
    else:
        log(handle, f"quality_report_failed run={run_dir.name} error={result.stderr.strip()!r}")


def acquire_lock(lock_path: Path) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            old_pid = int(lock_path.read_text(encoding="utf-8").strip())
            os.kill(old_pid, 0)
        except (OSError, ValueError):
            lock_path.unlink(missing_ok=True)
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        else:
            raise RuntimeError(f"overnight controller already running as PID {old_pid}")
    os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
    os.close(descriptor)
    return os.getpid()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--hours", type=float, default=12.0)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--min-score", type=int, default=12)
    parser.add_argument("--batch-limit", type=int, default=1)
    parser.add_argument("--batch-max-hours", type=float, default=8.0)
    parser.add_argument("--min-free-gb", type=float, default=12.0)
    parser.add_argument("--retry-job", default=DEFAULT_RETRY_JOB)
    args = parser.parse_args(argv)
    parser.error("Legacy controller is retired: it ignored failed quality gates. Use queue-archive-quality-rerun.py --apply-plan.")

    db_path = args.db.expanduser().resolve()
    runs_dir = args.runs_dir.expanduser().resolve()
    runs_dir.mkdir(parents=True, exist_ok=True)
    session_name = datetime.now().strftime("%Y%m%d-%H%M%S")
    session_dir = runs_dir / f"{session_name}-overnight-controller"
    session_dir.mkdir(parents=True, exist_ok=False)
    log_path = session_dir / "overnight.log"
    lock_path = runs_dir / ".overnight-quality-reruns.pid"
    acquire_lock(lock_path)
    deadline = datetime.now(timezone.utc) + timedelta(hours=args.hours)
    stop_requested = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    with log_path.open("a", encoding="utf-8") as handle:
        try:
            log(
                handle,
                f"controller_started pid={os.getpid()} deadline={deadline.isoformat()} "
                f"min_score={args.min_score} min_free_gb={args.min_free_gb}",
            )
            if not wait_for_idle(db_path, args.poll_seconds, deadline, handle):
                log(handle, "deadline_reached_while_waiting_for_initial_idle")
                return 0

            retry_dir = retry_failed_job(
                db_path,
                args.api_base,
                args.retry_job,
                runs_dir,
                handle,
            ) if args.retry_job else None
            if retry_dir is not None:
                if not wait_for_jobs(db_path, [args.retry_job], args.poll_seconds, deadline, handle):
                    log(handle, "deadline_reached_during_retry")
                    return 0

            batch_number = 1
            while not stop_requested and datetime.now(timezone.utc) < deadline:
                free_gb = shutil.disk_usage(db_path.parent).free / (1024 ** 3)
                if free_gb < args.min_free_gb:
                    log(handle, f"stopped_low_disk free_gb={free_gb:.2f}")
                    break
                if not wait_for_idle(db_path, args.poll_seconds, deadline, handle):
                    log(handle, "deadline_reached_while_waiting_for_idle")
                    break

                run_dir, job_ids = queue_batch(
                    db_path,
                    args.api_base,
                    runs_dir,
                    batch_number,
                    args.min_score,
                    args.batch_limit,
                    args.batch_max_hours,
                    handle,
                )
                if run_dir is None or not job_ids:
                    break
                log(handle, f"batch_queued number={batch_number} jobs={len(job_ids)} run={run_dir.name}")
                finished = wait_for_jobs(db_path, job_ids, args.poll_seconds, deadline, handle)
                if not finished:
                    log(handle, f"deadline_reached_during_batch number={batch_number}")
                    break
                write_quality_report(run_dir, db_path, handle)
                batch_number += 1

            log(handle, f"controller_finished stop_requested={stop_requested} batches_started={batch_number - 1}")
            return 0
        finally:
            lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
