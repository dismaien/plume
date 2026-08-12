#!/usr/bin/env python3
"""Move high-confidence Plume dream markers immediately after date/time.

Candidates must already contain ``(dream)`` or have a source filename/title
explicitly labelled as a dream. Updates go through Plume's title API so bundle
directories and metadata paths move with the database title.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


DEFAULT_DB = Path.home() / "Library/Application Support/plume-desktop/data/quill.db"
DEFAULT_API_BASE = "http://127.0.0.1:59152"
DATE_PREFIX_RE = re.compile(r"^(?P<prefix>\d{6}\s+\d{2}:?\d{2})\s*(?P<body>.*)$")
DREAM_MARKER_RE = re.compile(r"\s*\(dream\)\s*", re.IGNORECASE)
DREAM_WORD_RE = re.compile(r"(?:^|[^a-z0-9])dreams?(?:$|[^a-z0-9])", re.IGNORECASE)
DREAM_MUSIC_CONTEXT_RE = re.compile(r"\b(?:song|singing|lyrics?|music|dreamscape)\b", re.IGNORECASE)
DREAM_LEADING_RE = re.compile(
    r"^dream\b(?: account| note| recording)?(?:\s+of)?\s*(?:[:\-]\s*)?",
    re.IGNORECASE,
)


def source_stem(filename: str) -> str:
    return Path(filename or "").stem.strip()


def is_high_confidence_dream(title: str, original_filename: str) -> bool:
    explicit = bool(
        DREAM_MARKER_RE.search(title or "")
        or DREAM_WORD_RE.search(source_stem(original_filename))
        or re.search(r"\(dream(?:\b|!)", title or "", re.IGNORECASE)
    )
    title_signal = bool(
        DREAM_WORD_RE.search(title or "")
        and not DREAM_MUSIC_CONTEXT_RE.search(title or "")
    )
    return explicit or title_signal


def date_prefix(recorded_at: str, created_at: str) -> str:
    for raw in (recorded_at, created_at):
        if not raw:
            continue
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%y%m%d %H:%M")
        except ValueError:
            continue
    return ""


def normalize_title(title: str, original_filename: str, recorded_at: str, created_at: str) -> str:
    current = " ".join((title or "").split())
    match = DATE_PREFIX_RE.match(current)
    if match:
        prefix = match.group("prefix")
        body = match.group("body")
    else:
        prefix = date_prefix(recorded_at, created_at)
        body = current or source_stem(original_filename)

    body = " ".join(DREAM_MARKER_RE.sub(" ", body).split())
    if DREAM_WORD_RE.search(source_stem(original_filename)) or DREAM_WORD_RE.match(body):
        cleaned = DREAM_LEADING_RE.sub("", body).strip()
        if cleaned:
            body = cleaned
        elif body.lower() in {"dream", "dreams"}:
            body = ""

    pieces = [piece for piece in (prefix, "(dream)", body) if piece]
    return " ".join(pieces)


def update_title(api_base: str, job_id: str, title: str) -> tuple[bool, str]:
    request = urllib.request.Request(
        f"{api_base.rstrip('/')}/api/v1/transcription/{job_id}/title",
        data=json.dumps({"title": title}).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return 200 <= response.status < 300, response.read().decode(errors="replace")
    except (urllib.error.HTTPError, OSError) as error:
        detail = error.read().decode(errors="replace") if isinstance(error, urllib.error.HTTPError) else str(error)
        return False, detail


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(Path(args.db).expanduser())
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, title, original_filename, recorded_at, created_at
        FROM transcription_jobs
        WHERE deleted_at IS NULL
        ORDER BY recorded_at DESC, created_at DESC
        """
    ).fetchall()

    changed = 0
    failed = 0
    print("job_id\told_title\tnew_title\tapplied")
    for row in rows:
        old_title = row["title"] or ""
        original_filename = row["original_filename"] or ""
        if not is_high_confidence_dream(old_title, original_filename):
            continue
        new_title = normalize_title(
            old_title,
            original_filename,
            row["recorded_at"] or "",
            row["created_at"] or "",
        )
        if not new_title or new_title == old_title:
            continue

        applied = False
        if args.apply:
            applied, detail = update_title(args.api_base, row["id"], new_title)
            if not applied:
                failed += 1
                print(f"# update failed for {row['id']}: {detail[:500]}", file=sys.stderr)
        print(f"{row['id']}\t{old_title}\t{new_title}\t{applied}")
        changed += 1

    print(f"# candidates_changed={changed} failed={failed} apply={args.apply}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
