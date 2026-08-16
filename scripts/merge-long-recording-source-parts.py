#!/usr/bin/env python3
"""Merge long-recording chunk jobs back into parent Plume recordings.

This is a local repair tool for the voice memo archive import. It uses the
long-recording chunk manifest as the source of truth, marks chunk jobs as
internal source parts, and creates or updates one visible parent job per
original recording. Audio is referenced in place; this script does not copy or
delete audio files.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB = Path.home() / "Library/Application Support/plume-desktop/data/quill.db"
DEFAULT_MANIFEST = (
    Path.home()
    / "Documents/Voice Notes Archive/20-quill-processing/long-recording-chunks/manifest.json"
)
DEFAULT_VAULT = Path.home() / "Documents/Quill Vault"
DEFAULT_REPORT_DIR = (
    Path.home() / "Documents/Voice Notes Archive/20-quill-processing/long-recording-chunks/reports"
)

PART_TITLE_RE = re.compile(
    r"^(?P<prefix>\d{6} \d{2}:\d{2}) part (?P<idx>\d{1,2}) of (?P<count>\d{1,2}) \((?P<parent>.+)\)$",
    re.IGNORECASE,
)
DATE_PREFIX_RE = re.compile(r"^(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2}) (?P<hh>\d{2}):(?P<minute>\d{2})")
SQLITE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sqlite_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")


def parse_recorded_at(title: str) -> str | None:
    match = DATE_PREFIX_RE.match(title or "")
    if not match:
        return None
    yy = int(match.group("yy"))
    year = 2000 + yy
    try:
        dt = datetime(
            year,
            int(match.group("mm")),
            int(match.group("dd")),
            int(match.group("hh")),
            int(match.group("minute")),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S+00:00")


def duration_label(seconds: int | float | None) -> str:
    total = max(0, int(round(seconds or 0)))
    hours, rem = divmod(total, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}"


def sanitize_bundle_name(title: str) -> str:
    name = re.sub(r"^(\d{6} \d{2}):(\d{2})", r"\1\2", title.strip())
    name = re.sub(r"[/\\:\0]", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "Untitled Recording"
    if len(name) > 170:
        name = name[:170].rstrip()
    return name


def quote_ident(column: str) -> str:
    if not SQLITE_IDENTIFIER_RE.match(column):
        raise ValueError(f"unsafe SQLite identifier: {column!r}")
    return f'"{column}"'


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({quote_ident(table)})")}


def ensure_source_part_columns(conn: sqlite3.Connection) -> None:
    existing = table_columns(conn, "transcription_jobs")
    additions = {
        "is_source_part": "boolean DEFAULT false",
        "source_parent_id": "varchar(36)",
        "source_group_id": "text",
        "source_part_index": "int DEFAULT 0",
        "source_part_count": "int DEFAULT 0",
    }
    for column, spec in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE transcription_jobs ADD COLUMN {quote_ident(column)} {spec}")


def backup_database(db_path: Path) -> dict[str, str]:
    stamp = now_stamp()
    copied: dict[str, str] = {}
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db_path) + suffix)
        if not src.exists():
            continue
        dst = Path(str(db_path) + f".backup-{stamp}-before-long-part-merge{suffix}")
        shutil.copy2(src, dst)
        copied[str(src)] = str(dst)
    if not copied:
        raise FileNotFoundError(f"database not found: {db_path}")
    return copied


def row_to_dict(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {desc[0]: row[idx] for idx, desc in enumerate(cursor.description)}


def fetch_one_dict(conn: sqlite3.Connection, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    cur = conn.execute(query, params)
    row = cur.fetchone()
    if row is None:
        return None
    return row_to_dict(cur, row)


def fetch_chunk_job(conn: sqlite3.Connection, manifest_row: dict[str, Any]) -> dict[str, Any] | None:
    title = manifest_row.get("chunk_title", "")
    job = fetch_one_dict(
        conn,
        """
        SELECT * FROM transcription_jobs
        WHERE deleted_at IS NULL AND title = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (title,),
    )
    if job:
        return job

    original_filename = Path(manifest_row.get("chunk_path", "")).name
    if not original_filename:
        return None
    return fetch_one_dict(
        conn,
        """
        SELECT * FROM transcription_jobs
        WHERE deleted_at IS NULL AND original_filename = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (original_filename,),
    )


def load_transcript(job: dict[str, Any]) -> dict[str, Any] | None:
    raw = job.get("transcript")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

    json_path = job.get("transcript_json_path")
    if json_path and Path(json_path).exists():
        try:
            return json.loads(Path(json_path).read_text())
        except (OSError, json.JSONDecodeError):
            return None

    artifact_dir = job.get("artifact_dir")
    if artifact_dir:
        candidate = Path(artifact_dir) / "transcript.json"
        if candidate.exists():
            try:
                return json.loads(candidate.read_text())
            except (OSError, json.JSONDecodeError):
                return None

    return None


def offset_segment(segment: dict[str, Any], offset: float) -> dict[str, Any]:
    adjusted = copy.deepcopy(segment)
    for key in ("start", "end"):
        if isinstance(adjusted.get(key), (int, float)):
            adjusted[key] = adjusted[key] + offset
    return adjusted


def offset_word(word: dict[str, Any], offset: float) -> dict[str, Any]:
    adjusted = copy.deepcopy(word)
    for key in ("start", "end"):
        if isinstance(adjusted.get(key), (int, float)):
            adjusted[key] = adjusted[key] + offset
    return adjusted


def merge_transcripts(parts: list[dict[str, Any]]) -> dict[str, Any] | None:
    merged_segments: list[dict[str, Any]] = []
    merged_words: list[dict[str, Any]] = []
    texts: list[str] = []
    languages: list[str] = []
    models: list[str] = []
    processing_time = 0.0

    for part in sorted(parts, key=lambda p: int(p["manifest"].get("chunk_index", 0))):
        transcript = part.get("transcript")
        if not transcript:
            return None
        offset = float(part["manifest"].get("chunk_start_seconds") or 0)

        text = transcript.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
        language = transcript.get("language")
        if isinstance(language, str) and language and language not in languages:
            languages.append(language)
        model = transcript.get("model_used")
        if isinstance(model, str) and model and model not in models:
            models.append(model)
        if isinstance(transcript.get("processing_time"), (int, float)):
            processing_time += float(transcript["processing_time"])

        for segment in transcript.get("segments") or []:
            if isinstance(segment, dict):
                merged_segments.append(offset_segment(segment, offset))
        for word in transcript.get("word_segments") or []:
            if isinstance(word, dict):
                merged_words.append(offset_word(word, offset))

    result: dict[str, Any] = {
        "text": "\n\n".join(texts),
        "language": languages[0] if len(languages) == 1 else (", ".join(languages) if languages else ""),
        "segments": merged_segments,
        "word_segments": merged_words,
        "confidence": 0,
        "processing_time": processing_time,
        "model_used": models[0] if len(models) == 1 else (", ".join(models) if models else ""),
        "metadata": {
            "adapter_type": "long_recording_part_merge",
            "merged_at": iso_now(),
            "source_part_count": len(parts),
        },
    }
    return result


def speaker_label(speaker: Any) -> str:
    if not isinstance(speaker, str) or not speaker.strip():
        return "Unknown"
    normalized = speaker.strip()
    match = re.fullmatch(r"speaker[_ -]?(\d+)", normalized, re.IGNORECASE)
    if match:
        index = int(match.group(1))
        if 0 <= index < 26:
            return f"Speaker {chr(ord('A') + index)}"
    return normalized


def timestamp_label(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)):
        seconds = 0
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def write_parent_bundle(
    vault: Path,
    parent_id: str,
    title: str,
    manifest_rows: list[dict[str, Any]],
    merged: dict[str, Any] | None,
) -> tuple[str | None, str | None, str | None]:
    if merged is None:
        return None, None, None

    artifact_dir = vault / "Transcripts" / sanitize_bundle_name(title)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    json_path = artifact_dir / "transcript.json"
    md_path = artifact_dir / "transcript.md"
    metadata_path = artifact_dir / "metadata.json"

    json_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n")

    first = manifest_rows[0]
    metadata = {
        "id": parent_id,
        "title": title,
        "status": "completed",
        "source_audio_path": first.get("source_path"),
        "source_audio_filename": first.get("apple_path") or Path(first.get("source_path", "")).name,
        "source_duration_seconds": first.get("source_duration_seconds"),
        "source_group_id": first.get("source_job_id"),
        "source_part_count": len(manifest_rows),
        "source_parts": [
            {
                "index": row.get("chunk_index"),
                "total": row.get("chunk_total"),
                "start_seconds": row.get("chunk_start_seconds"),
                "duration_seconds": row.get("chunk_duration_seconds"),
                "title": row.get("chunk_title"),
                "path": row.get("chunk_path"),
            }
            for row in sorted(manifest_rows, key=lambda r: int(r.get("chunk_index", 0)))
        ],
        "merged_at": iso_now(),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")

    lines = [
        f"# {title}",
        "",
        "## Overview",
        "",
        f"- Date/time: {title[:12] if DATE_PREFIX_RE.match(title) else ''}".rstrip(),
        f"- Duration: {duration_label(first.get('source_duration_seconds'))}",
        f"- Source audio: {first.get('apple_path') or Path(first.get('source_path', '')).name}",
        f"- Transcription: merged from {len(manifest_rows)} internal chunk files",
        "",
        "## Transcript",
        "",
    ]

    for segment in merged.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        lines.extend(
            [
                f"### [{timestamp_label(segment.get('start'))}] {speaker_label(segment.get('speaker'))}",
                "",
                text,
                "",
            ]
        )

    md_path.write_text("\n".join(lines).rstrip() + "\n")
    return str(artifact_dir), str(json_path), str(md_path)


def infer_group_status(parts: list[dict[str, Any]], manifest_rows: list[dict[str, Any]]) -> str:
    if len(parts) != len(manifest_rows):
        return "uploaded"
    if all(part.get("job", {}).get("status") == "completed" and part.get("transcript") for part in parts):
        return "completed"
    return "uploaded"


def upsert_parent(
    conn: sqlite3.Connection,
    parent_id: str,
    title: str,
    manifest_rows: list[dict[str, Any]],
    parts: list[dict[str, Any]],
    vault_id: int | None,
    vault: Path,
    dry_run: bool,
) -> dict[str, Any]:
    existing = fetch_one_dict(conn, "SELECT * FROM transcription_jobs WHERE id = ? LIMIT 1", (parent_id,))
    first = manifest_rows[0]
    status = infer_group_status(parts, manifest_rows)
    merged = merge_transcripts(parts) if status == "completed" else None
    artifact_dir, json_path, md_path = (None, None, None)
    if not dry_run:
        artifact_dir, json_path, md_path = write_parent_bundle(vault, parent_id, title, manifest_rows, merged)
    elif merged is not None:
        artifact_dir = str(vault / "Transcripts" / sanitize_bundle_name(title))
        json_path = str(Path(artifact_dir) / "transcript.json")
        md_path = str(Path(artifact_dir) / "transcript.md")

    source_path = first.get("source_path") or ""
    recorded_at = parse_recorded_at(title)
    created_at = existing.get("created_at") if existing else (recorded_at or sqlite_now())
    original_filename = first.get("apple_path") or Path(source_path).name
    file_hash = first.get("source_hash") or (existing.get("file_hash") if existing else "")
    diarization = int(any(part.get("job", {}).get("diarization") for part in parts))
    transcript_json = json.dumps(merged, ensure_ascii=False) if merged is not None else None

    if dry_run:
        action = "update" if existing else "insert"
    elif existing:
        action = "update"
        conn.execute(
            """
            UPDATE transcription_jobs
            SET title = ?,
                status = ?,
                audio_path = ?,
                vault_id = COALESCE(vault_id, ?),
                artifact_dir = ?,
                transcript_json_path = ?,
                transcript_markdown_path = ?,
                transcript = ?,
                diarization = ?,
                original_filename = CASE WHEN original_filename IS NULL OR original_filename = '' THEN ? ELSE original_filename END,
                file_hash = CASE WHEN file_hash IS NULL OR file_hash = '' THEN ? ELSE file_hash END,
                recorded_at = COALESCE(recorded_at, ?),
                is_source_part = 0,
                source_group_id = ?,
                source_parent_id = '',
                source_part_index = 0,
                source_part_count = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                title,
                status,
                source_path,
                vault_id,
                artifact_dir,
                json_path,
                md_path,
                transcript_json,
                diarization,
                original_filename,
                file_hash,
                recorded_at,
                parent_id,
                len(manifest_rows),
                sqlite_now(),
                parent_id,
            ),
        )
    else:
        action = "insert"
        conn.execute(
            """
            INSERT INTO transcription_jobs (
                id, title, status, audio_path, vault_id,
                artifact_dir, transcript_json_path, transcript_markdown_path,
                transcript, diarization, summary, original_filename, file_hash,
                recorded_at, is_source_part, source_group_id, source_parent_id,
                source_part_index, source_part_count, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 0, ?, '', 0, ?, ?, ?)
            """,
            (
                parent_id,
                title,
                status,
                source_path,
                vault_id,
                artifact_dir,
                json_path,
                md_path,
                transcript_json,
                diarization,
                original_filename,
                file_hash,
                recorded_at,
                parent_id,
                len(manifest_rows),
                created_at,
                sqlite_now(),
            ),
        )

    return {
        "id": parent_id,
        "title": title,
        "status": status,
        "action": action,
        "source_path": source_path,
        "source_path_exists": bool(source_path and Path(source_path).exists()),
        "chunk_count": len(manifest_rows),
        "chunk_jobs_found": len(parts),
        "merged_transcript": merged is not None,
        "artifact_dir": artifact_dir,
        "transcript_json_path": json_path,
        "transcript_markdown_path": md_path,
    }


def mark_source_parts(
    conn: sqlite3.Connection,
    parent_id: str,
    manifest_rows: list[dict[str, Any]],
    parts: list[dict[str, Any]],
    dry_run: bool,
) -> int:
    if dry_run:
        return len(parts)
    updated = 0
    by_title = {part["manifest"]["chunk_title"]: part for part in parts}
    for row in manifest_rows:
        part = by_title.get(row.get("chunk_title"))
        if not part:
            continue
        job_id = part["job"]["id"]
        conn.execute(
            """
            UPDATE transcription_jobs
            SET is_source_part = 1,
                source_parent_id = ?,
                source_group_id = ?,
                source_part_index = ?,
                source_part_count = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                parent_id,
                parent_id,
                int(row.get("chunk_index") or 0),
                int(row.get("chunk_total") or len(manifest_rows)),
                sqlite_now(),
                job_id,
            ),
        )
        updated += 1
    return updated


def choose_parent_title(existing: dict[str, Any] | None, manifest_rows: list[dict[str, Any]]) -> str:
    if existing and existing.get("title") and not PART_TITLE_RE.match(existing["title"]):
        return existing["title"]
    return manifest_rows[0].get("source_title") or manifest_rows[0].get("chunk_title") or manifest_rows[0].get("source_job_id")


def active_vault_id(conn: sqlite3.Connection) -> int | None:
    for query in (
        "SELECT id FROM vaults WHERE is_active = 1 LIMIT 1",
        "SELECT id FROM vaults ORDER BY id LIMIT 1",
    ):
        try:
            row = conn.execute(query).fetchone()
        except sqlite3.OperationalError:
            continue
        if row:
            return int(row[0])
    return None


def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest_rows = json.loads(args.manifest.read_text())
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in manifest_rows:
        groups[row["source_job_id"]].append(row)

    backup_paths = {} if args.dry_run or args.no_backup else backup_database(args.db)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        ensure_source_part_columns(conn)
        vault_id = active_vault_id(conn)
        report: dict[str, Any] = {
            "started_at": iso_now(),
            "dry_run": args.dry_run,
            "database": str(args.db),
            "manifest": str(args.manifest),
            "vault": str(args.vault),
            "backup_paths": backup_paths,
            "source_group_count": len(groups),
            "manifest_chunk_count": len(manifest_rows),
            "parents": [],
            "missing_chunk_jobs": [],
        }

        for parent_id, rows in sorted(groups.items(), key=lambda item: item[1][0].get("source_title", "")):
            rows = sorted(rows, key=lambda r: int(r.get("chunk_index") or 0))
            existing_parent = fetch_one_dict(conn, "SELECT * FROM transcription_jobs WHERE id = ? LIMIT 1", (parent_id,))
            parent_title = choose_parent_title(existing_parent, rows)

            parts: list[dict[str, Any]] = []
            for row in rows:
                job = fetch_chunk_job(conn, row)
                if not job:
                    report["missing_chunk_jobs"].append(
                        {
                            "source_group_id": parent_id,
                            "source_title": parent_title,
                            "chunk_index": row.get("chunk_index"),
                            "chunk_title": row.get("chunk_title"),
                        }
                    )
                    continue
                parts.append({"manifest": row, "job": job, "transcript": load_transcript(job)})

            parent_report = upsert_parent(conn, parent_id, parent_title, rows, parts, vault_id, args.vault, args.dry_run)
            parent_report["source_parts_marked"] = mark_source_parts(conn, parent_id, rows, parts, args.dry_run)
            report["parents"].append(parent_report)

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()

        report["finished_at"] = iso_now()
        return report
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"database not found: {args.db}", file=sys.stderr)
        return 2
    if not args.manifest.exists():
        print(f"manifest not found: {args.manifest}", file=sys.stderr)
        return 2

    report = run(args)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / f"merge-long-recording-source-parts-{now_stamp()}{'-dry-run' if args.dry_run else ''}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    completed = sum(1 for parent in report["parents"] if parent["status"] == "completed")
    uploaded = sum(1 for parent in report["parents"] if parent["status"] == "uploaded")
    merged = sum(1 for parent in report["parents"] if parent["merged_transcript"])
    print(f"report: {report_path}")
    print(f"parents: {len(report['parents'])} ({completed} completed, {uploaded} uploaded)")
    print(f"merged transcripts: {merged}")
    print(f"source parts marked: {sum(parent['source_parts_marked'] for parent in report['parents'])}")
    print(f"missing chunk jobs: {len(report['missing_chunk_jobs'])}")
    if report.get("backup_paths"):
        print("database backup:")
        for src, dst in report["backup_paths"].items():
            print(f"  {src} -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
