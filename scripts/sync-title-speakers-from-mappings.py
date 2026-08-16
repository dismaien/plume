#!/usr/bin/env python3
"""Synchronize bracketed speaker labels in Plume recording titles.

This repairs titles after speaker mappings change. It replaces only the derived
speaker block near the end of the title:

  260706 13:53 Is This Safe [Duncan] @Rua Regedoura Guindais {00:43}
  -> 260706 13:53 Is This Safe [Ivan] @Rua Regedoura Guindais {00:43}

The script is dry-run by default. Use --apply to update the Plume SQLite DB,
rename transcript bundle folders, and refresh title fields in sidecars.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB = Path.home() / "Library/Application Support/plume-desktop/data/quill.db"
DEFAULT_REPORT_DIR = Path.home() / "Documents/Voice Notes Archive/20-quill-processing"

SPEAKER_BLOCK_RE = re.compile(r"\s*\[[^\[\]\n]*\](?=\s*(?:@|\{\d{1,2}:?\d{2}(?::\d{2})?\}\s*$|$))")
LOCATION_RE = re.compile(r"\s+@")
DURATION_RE = re.compile(r"\s+\{\d{1,2}:?\d{2}(?::\d{2})?\}\s*$")
UNSAFE_FILENAME_CHARS = {":", "/", "\\", "\x00", "<", ">", "|", '"', "?", "*"}
MAX_FILENAME_BYTES = 255


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sqlite_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")


def backup_database(db_path: Path) -> dict[str, str]:
    stamp = now_stamp()
    copied: dict[str, str] = {}
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db_path) + suffix)
        if not src.exists():
            continue
        dst = Path(str(db_path) + f".backup-{stamp}-before-speaker-title-sync{suffix}")
        shutil.copy2(src, dst)
        copied[str(src)] = str(dst)
    if not copied:
        raise FileNotFoundError(f"database not found: {db_path}")
    return copied


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


def path_inside(path: str | None, parent: str | None) -> bool:
    if not path or not parent:
        return False
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except (OSError, ValueError):
        return False


def rebase_path(path: str | None, old_parent: str, new_parent: str) -> str | None:
    if not path:
        return path
    try:
        relative = Path(path).resolve().relative_to(Path(old_parent).resolve())
    except (OSError, ValueError):
        return path
    return str(Path(new_parent) / relative)


def is_generic_speaker_label(name: str) -> bool:
    normalized = " ".join((name or "").strip().lower().replace("_", " ").replace("-", " ").split())
    return normalized in {"", "unknown", "speaker"} or normalized.startswith("speaker ")


def speaker_title_name(display_name: str) -> str:
    name = (display_name or "").strip()
    if not name or is_generic_speaker_label(name):
        return ""
    first = name.split()[0].strip(" \t\r\n,;:")
    if not first or is_generic_speaker_label(first):
        return ""
    return first


def normalize_speaker_names(names: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for name in names:
        short = speaker_title_name(name)
        key = short.lower()
        if not short or key in seen:
            continue
        seen.add(key)
        cleaned.append(short)
    return cleaned


def speaker_names_from_transcript(raw_transcript: str, mappings: list[sqlite3.Row]) -> list[str]:
    by_original = {
        (row["original_speaker"] or "").strip(): (row["custom_name"] or "").strip()
        for row in mappings
        if (row["original_speaker"] or "").strip()
        and (row["custom_name"] or "").strip()
        and not is_generic_speaker_label(row["custom_name"] or "")
    }

    names: list[str] = []
    if raw_transcript.strip():
        try:
            payload = json.loads(raw_transcript)
        except json.JSONDecodeError:
            payload = {}
        for segment in payload.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            speaker = str(segment.get("speaker") or "").strip()
            if speaker and speaker in by_original:
                names.append(by_original[speaker])
                continue
            speaker_name = str(segment.get("speaker_name") or "").strip()
            if speaker_name:
                names.append(speaker_name)

    for row in sorted(mappings, key=lambda item: item["original_speaker"] or ""):
        names.append(row["custom_name"] or "")

    return normalize_speaker_names(names)


def with_speaker_title_block(title: str, speakers: list[str]) -> str:
    cleaned = normalize_speaker_names(speakers)
    trimmed = (title or "").strip()
    if not cleaned:
        return trimmed
    block = f" [{', '.join(cleaned)}]"

    match = SPEAKER_BLOCK_RE.search(trimmed)
    if match:
        return trimmed[: match.start()].strip() + block + trimmed[match.end() :]

    insert_at = len(trimmed)
    loc_match = LOCATION_RE.search(trimmed)
    if loc_match:
        insert_at = loc_match.start()
    dur_match = DURATION_RE.search(trimmed)
    if dur_match and dur_match.start() < insert_at:
        insert_at = dur_match.start()

    return (trimmed[:insert_at].strip() + block + trimmed[insert_at:]).strip()


def read_transcript_for_job(job: sqlite3.Row) -> str:
    raw = (job["transcript"] or "").strip()
    if raw:
        return raw
    json_path = job["transcript_json_path"]
    if json_path and Path(json_path).exists():
        try:
            return Path(json_path).read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


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


def update_markdown_title(md_path: str | None, new_title: str) -> bool:
    if not md_path:
        return False
    path = Path(md_path)
    if not path.exists():
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False

    new_content = content
    if new_content.startswith("---\n"):
        end = new_content.find("\n---\n", 4)
        if end != -1:
            front = new_content[:end]
            rest = new_content[end:]
            if re.search(r"(?m)^title:\s*.*$", front):
                front = re.sub(r"(?m)^title:\s*.*$", f"title: {json.dumps(new_title, ensure_ascii=False)}", front, count=1)
            else:
                front += f"\ntitle: {json.dumps(new_title, ensure_ascii=False)}"
            new_content = front + rest

    new_content = re.sub(r"(?m)^# .*$", f"# {new_title}", new_content, count=1)
    if new_content == content:
        return False
    path.write_text(new_content, encoding="utf-8")
    return True


def update_metadata_title(artifact_dir: str | None, new_title: str) -> bool:
    if not artifact_dir:
        return False
    path = Path(artifact_dir) / "metadata.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("title") == new_title:
        return False
    data["title"] = new_title
    data["updated_at"] = iso_now()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def load_jobs(conn: sqlite3.Connection, job_id: str | None) -> list[sqlite3.Row]:
    query = """
        SELECT id, title, transcript, summary, audio_path, artifact_dir,
               transcript_json_path, transcript_markdown_path
        FROM transcription_jobs
        WHERE deleted_at IS NULL
          AND (is_source_part = false OR is_source_part IS NULL)
    """
    params: tuple[Any, ...] = ()
    if job_id:
        query += " AND id = ?"
        params = (job_id,)
    query += " ORDER BY recorded_at DESC, created_at DESC"
    return list(conn.execute(query, params))


def load_mappings(conn: sqlite3.Connection, job_id: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT original_speaker, custom_name
            FROM speaker_mappings
            WHERE transcription_job_id = ?
            ORDER BY original_speaker ASC
            """,
            (job_id,),
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


def sync_speaker_titles(args: argparse.Namespace) -> dict[str, int]:
    db_path = Path(args.db).expanduser()
    report_dir = Path(args.report_dir).expanduser()
    report_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = report_dir / f"quill-speaker-title-sync-{now_stamp()}.jsonl"

    counts = {
        "seen": 0,
        "with_mappings": 0,
        "changed": 0,
        "skipped_same": 0,
        "skipped_no_speakers": 0,
        "skipped_conflict": 0,
        "renamed_bundles": 0,
        "updated_markdown": 0,
        "updated_metadata": 0,
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
                    "event": "speaker_title_sync_start",
                    "db": str(db_path),
                    "apply": bool(args.apply),
                    "job_id": args.job_id or "",
                    "backups": backups,
                },
                ensure_ascii=False,
            )
            + "\n"
        )

        try:
            for job in load_jobs(conn, args.job_id):
                counts["seen"] += 1
                mappings = load_mappings(conn, job["id"])
                if not mappings:
                    continue
                counts["with_mappings"] += 1

                speakers = speaker_names_from_transcript(read_transcript_for_job(job), mappings)
                if not speakers:
                    counts["skipped_no_speakers"] += 1
                    continue

                old_title = job["title"] or ""
                new_title = with_speaker_title_block(old_title, speakers)
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
                    try:
                        same_dir = old_dir.resolve() == target_dir.resolve()
                    except OSError:
                        same_dir = False
                    if old_dir.exists() and not same_dir:
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

                    if update_markdown_title(new_markdown_path, new_title):
                        counts["updated_markdown"] += 1
                    if update_metadata_title(new_artifact_dir, new_title):
                        counts["updated_metadata"] += 1

                counts["changed"] += 1
                ledger.write(
                    json.dumps(
                        {
                            "at": iso_now(),
                            "event": "speaker_title_sync_update" if args.apply else "speaker_title_sync_dryrun",
                            "job_id": job["id"],
                            "old_title": old_title,
                            "new_title": new_title,
                            "speakers": speakers,
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
                    "event": "speaker_title_sync_done",
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
    parser.add_argument("--job-id", default="", help="Optional single transcription job id")
    parser.add_argument("--apply", action="store_true", help="Apply changes; default is dry-run")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    sync_speaker_titles(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
