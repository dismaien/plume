#!/usr/bin/env python3
"""Synchronize trailing duration labels in Plume recording titles.

Voice memo archive titles previously used minute-level labels such as
``{00:06}``, which is ambiguous for short recordings and visibly disagrees with
the exact duration shown by Plume. This local repair tool replaces only the
trailing duration label with an exact clock duration, using the same source
order as the app: audio metadata first, then transcript segment end time when
the audio file is unavailable.

Examples:
  260810 09:31 Visiting Dreams [Leona] {00:06}
  -> 260810 09:31 Visiting Dreams [Leona] {05:38}

  260604 19:12 Long Conversation {07:13}
  -> 260604 19:12 Long Conversation {07:13:42}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB = Path.home() / "Library/Application Support/plume-desktop/data/quill.db"
DEFAULT_REPORT_DIR = Path.home() / "Documents/Voice Notes Archive/20-quill-processing"
DEFAULT_FFPROBE = Path("/Applications/Plume.app/Contents/Resources/tools/ffprobe")

DURATION_SUFFIX_RE = re.compile(r"\s*\{(?P<label>\d{1,2}:?\d{2}(?::\d{2})?)\}\s*$")
UNSAFE_FILENAME_CHARS = {":", "/", "\\", "\x00", "<", ">", "|", '"', "?", "*"}
MAX_FILENAME_BYTES = 255


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sqlite_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")


def safe_filename(value: str, fallback: str = "Transcript") -> str:
    trimmed = (value or "").strip()
    if not trimmed:
        return fallback

    out: list[str] = []
    last_space = False
    for char in trimmed:
        if char in UNSAFE_FILENAME_CHARS:
            continue
        if char.isspace():
            if not last_space:
                out.append(" ")
                last_space = True
            continue
        out.append(char)
        last_space = False

    result = "".join(out).strip().strip(".").strip()
    if not result:
        return fallback

    encoded = result.encode("utf-8")
    while len(encoded) > MAX_FILENAME_BYTES and result:
        result = result[:-1]
        encoded = result.encode("utf-8")
    result = result.strip().rstrip(".").strip()
    return result or fallback


def backup_database(db_path: Path) -> dict[str, str]:
    stamp = now_stamp()
    copied: dict[str, str] = {}
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db_path) + suffix)
        if not src.exists():
            continue
        dst = Path(str(db_path) + f".backup-{stamp}-before-duration-label-sync{suffix}")
        shutil.copy2(src, dst)
        copied[str(src)] = str(dst)
    if not copied:
        raise FileNotFoundError(f"database not found: {db_path}")
    return copied


def duration_seconds_from_transcript(raw: str | None) -> float:
    if not raw or not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    max_end = 0.0
    for segment in payload.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        end = segment.get("end")
        if isinstance(end, (int, float)) and end > max_end:
            max_end = float(end)
    return max_end


def duration_seconds_from_audio(ffprobe: Path, audio_path: str | None) -> float:
    if not audio_path:
        return 0
    path = Path(audio_path)
    if not path.exists() or not path.is_file():
        return 0
    try:
        output = subprocess.check_output(
            [
                str(ffprobe),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    try:
        return max(0.0, float(output.strip()))
    except ValueError:
        return 0


def exact_duration_label(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def with_exact_duration_label(title: str, duration_seconds: float) -> str:
    label = exact_duration_label(duration_seconds)
    return DURATION_SUFFIX_RE.sub(f" {{{label}}}", title.strip())


def title_has_duration_suffix(title: str | None) -> bool:
    return bool(title and DURATION_SUFFIX_RE.search(title))


def extract_plain_text(transcript: str | None) -> str:
    if not transcript:
        return ""
    try:
        payload = json.loads(transcript)
    except json.JSONDecodeError:
        return transcript
    texts: list[str] = []
    for segment in payload.get("segments") or []:
        if isinstance(segment, dict):
            text = str(segment.get("text") or "").strip()
            if text:
                texts.append(text)
    return " ".join(texts) if texts else str(payload.get("text") or "")


def path_inside(path: str | None, parent: str | None) -> bool:
    if not path or not parent:
        return False
    try:
        return Path(path).resolve().is_relative_to(Path(parent).resolve())
    except OSError:
        return False


def rebase_path(path: str | None, old_parent: str, new_parent: str) -> str | None:
    if not path:
        return path
    old = Path(old_parent)
    candidate = Path(path)
    try:
        relative = candidate.resolve().relative_to(old.resolve())
    except (OSError, ValueError):
        return path
    return str(Path(new_parent) / relative)


def load_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(
        conn.execute(
            """
            SELECT id, title, status, transcript, summary, audio_path, artifact_dir,
                   transcript_json_path, transcript_markdown_path
            FROM transcription_jobs
            WHERE deleted_at IS NULL
              AND (is_source_part = false OR is_source_part IS NULL)
            ORDER BY recorded_at DESC, created_at DESC
            """
        )
    )


def upsert_fts(conn: sqlite3.Connection, job: sqlite3.Row, new_title: str) -> None:
    content = extract_plain_text(job["transcript"])
    summary = job["summary"] or ""
    conn.execute("DELETE FROM transcription_fts WHERE job_id = ?", (job["id"],))
    conn.execute(
        "INSERT INTO transcription_fts(job_id, title, content, summary) VALUES (?, ?, ?, ?)",
        (job["id"], new_title, content, summary),
    )


def sync_duration_labels(args: argparse.Namespace) -> dict[str, int]:
    db_path = Path(args.db).expanduser()
    report_dir = Path(args.report_dir).expanduser()
    report_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = report_dir / f"quill-duration-title-exact-sync-{now_stamp()}.jsonl"

    ffprobe = Path(args.ffprobe).expanduser()
    if not ffprobe.exists():
        ffprobe = Path(shutil.which("ffprobe") or "")

    counts = {
        "seen": 0,
        "with_suffix": 0,
        "changed": 0,
        "skipped_no_duration": 0,
        "skipped_same": 0,
        "skipped_conflict": 0,
        "skipped_active": 0,
        "renamed_bundles": 0,
    }

    backups: dict[str, str] = {}
    if args.apply:
        backups = backup_database(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    with ledger_path.open("w", encoding="utf-8") as ledger:
        ledger.write(
            json.dumps(
                {
                    "at": iso_now(),
                    "event": "duration_title_exact_sync_start",
                    "db": str(db_path),
                    "apply": bool(args.apply),
                    "backups": backups,
                    "ffprobe": str(ffprobe) if ffprobe else "",
                },
                ensure_ascii=False,
            )
            + "\n"
        )

        try:
            for job in load_jobs(conn):
                counts["seen"] += 1
                old_title = job["title"] or ""
                if not title_has_duration_suffix(old_title):
                    continue
                counts["with_suffix"] += 1

                if not args.include_active and str(job["status"]) in {"pending", "processing"}:
                    counts["skipped_active"] += 1
                    ledger.write(
                        json.dumps(
                            {
                                "at": iso_now(),
                                "event": "skip_active_job",
                                "job_id": job["id"],
                                "status": job["status"],
                                "title": old_title,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    continue

                duration_seconds = 0.0
                source = ""
                if ffprobe:
                    duration_seconds = duration_seconds_from_audio(ffprobe, job["audio_path"])
                    source = "audio"
                if duration_seconds <= 0:
                    duration_seconds = duration_seconds_from_transcript(job["transcript"])
                    source = "transcript"

                if duration_seconds <= 0:
                    counts["skipped_no_duration"] += 1
                    ledger.write(
                        json.dumps(
                            {
                                "at": iso_now(),
                                "event": "skip_no_duration",
                                "job_id": job["id"],
                                "title": old_title,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    continue

                new_title = with_exact_duration_label(old_title, duration_seconds)
                if new_title == old_title:
                    counts["skipped_same"] += 1
                    continue

                old_artifact_dir = job["artifact_dir"]
                new_artifact_dir = old_artifact_dir
                new_audio_path = job["audio_path"]
                new_json_path = job["transcript_json_path"]
                new_markdown_path = job["transcript_markdown_path"]
                rename_from = ""
                rename_to = ""

                if old_artifact_dir:
                    old_dir = Path(old_artifact_dir)
                    target_dir = old_dir.parent / safe_filename(new_title)
                    if old_dir.exists() and old_dir.resolve() != target_dir.resolve():
                        if target_dir.exists():
                            counts["skipped_conflict"] += 1
                            ledger.write(
                                json.dumps(
                                    {
                                        "at": iso_now(),
                                        "event": "skip_bundle_conflict",
                                        "job_id": job["id"],
                                        "old_title": old_title,
                                        "new_title": new_title,
                                        "target_dir": str(target_dir),
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                            continue
                        rename_from = str(old_dir)
                        rename_to = str(target_dir)
                        new_artifact_dir = str(target_dir)
                        if path_inside(new_audio_path, old_artifact_dir):
                            new_audio_path = rebase_path(new_audio_path, old_artifact_dir, new_artifact_dir)
                        if path_inside(new_json_path, old_artifact_dir):
                            new_json_path = rebase_path(new_json_path, old_artifact_dir, new_artifact_dir)
                        if path_inside(new_markdown_path, old_artifact_dir):
                            new_markdown_path = rebase_path(new_markdown_path, old_artifact_dir, new_artifact_dir)

                if args.apply and rename_from and rename_to:
                    os.rename(rename_from, rename_to)
                    counts["renamed_bundles"] += 1

                if args.apply:
                    conn.execute("BEGIN")
                    try:
                        conn.execute(
                            """
                            UPDATE transcription_jobs
                            SET title = ?,
                                audio_path = ?,
                                artifact_dir = ?,
                                transcript_json_path = ?,
                                transcript_markdown_path = ?,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                new_title,
                                new_audio_path,
                                new_artifact_dir,
                                new_json_path,
                                new_markdown_path,
                                sqlite_now(),
                                job["id"],
                            ),
                        )
                        upsert_fts(conn, job, new_title)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        if rename_from and rename_to and Path(rename_to).exists() and not Path(rename_from).exists():
                            os.rename(rename_to, rename_from)
                        raise

                counts["changed"] += 1
                ledger.write(
                    json.dumps(
                        {
                            "at": iso_now(),
                            "event": "duration_title_exact_sync_update" if args.apply else "duration_title_exact_sync_dryrun",
                            "job_id": job["id"],
                            "old_title": old_title,
                            "new_title": new_title,
                            "duration_seconds": round(duration_seconds, 3),
                            "duration_source": source,
                            "rename_from": rename_from,
                            "rename_to": rename_to,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        finally:
            conn.close()

        ledger.write(
            json.dumps(
                {
                    "at": iso_now(),
                    "event": "duration_title_exact_sync_done",
                    "counts": counts,
                    "ledger_path": str(ledger_path),
                },
                ensure_ascii=False,
            )
            + "\n"
        )

    print(json.dumps({"counts": counts, "ledger_path": str(ledger_path), "apply": bool(args.apply)}, indent=2))
    return counts


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to Plume quill.db")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR), help="Directory for JSONL ledger")
    parser.add_argument("--ffprobe", default=str(DEFAULT_FFPROBE), help="ffprobe executable path")
    parser.add_argument("--apply", action="store_true", help="Apply changes; default is dry-run")
    parser.add_argument(
        "--include-active",
        action="store_true",
        help="Also update pending/processing jobs. Default skips active jobs to avoid renaming files in use.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    sync_duration_labels(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
