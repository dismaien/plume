#!/usr/bin/env python3
"""Force-retitle completed Plume recordings with bad archive titles.

This is for live archive cleanup when the installed app's auto-title eligibility
logic skips rows such as Apple filename fallbacks. It uses local Ollama by
default, builds Leona's canonical title shape, then updates through Plume's
title API so bundle folders and sidecars are renamed by the app.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB = Path.home() / "Library/Application Support/plume-desktop/data/quill.db"
DEFAULT_API_BASE = "http://127.0.0.1:59152"
DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:7b"

RAW_APPLE_FILENAME_RE = re.compile(r"^\d{6} \d{2}:\d{2} 20\d{6} \d{6}-[0-9A-Fa-f]{8,}$")
DATE_PREFIX_RE = re.compile(r"^(?P<prefix>\d{6} \d{2}:\d{2})\s+(?P<body>.*)$")
DURATION_RE = re.compile(r"\s+\{(?P<label>\d{1,2}:?\d{2}(?::\d{2})?)\}\s*$")
SPEAKER_BLOCK_RE = re.compile(r"\s+\[[^\[\]\n]*\]")
LOCATION_RE = re.compile(r"\s+@(?P<location>[^{}]+?)(?=\s+\{|\s*$)")
FILENAME_LIKE_RE = re.compile(r"20\d{6}\s+\d{6}-[0-9A-Fa-f]{8,}")

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


@dataclass
class Candidate:
    id: str
    title: str
    recorded_at: str
    transcript_json_path: str
    summary: str
    labels: list[str]
    duration_seconds: float
    speakers: list[str]


def classify(title: str) -> list[str]:
    labels: list[str] = []
    stripped = (title or "").strip()
    lower = stripped.lower()
    if RAW_APPLE_FILENAME_RE.match(stripped):
        labels.append("raw_apple_filename")
    if "thank you" in lower:
        labels.append("thank_you_title")
    if not DURATION_RE.search(stripped):
        labels.append("missing_duration")

    subject = DATE_PREFIX_RE.sub(r"\g<body>", stripped).strip()
    subject = DURATION_RE.sub("", subject).strip()
    subject = SPEAKER_BLOCK_RE.sub("", subject).strip()
    subject = LOCATION_RE.sub("", subject).strip()
    subject_lower = subject.lower()
    if subject_lower in GENERIC_TERMS or any(subject_lower.startswith(term + " ") for term in GENERIC_TERMS):
        labels.append("generic_title")
    if any(subject_lower.startswith(prefix) for prefix in LOCATION_PREFIXES):
        labels.append("location_only")
    return labels


def first_name(name: str) -> str:
    cleaned = " ".join((name or "").strip().split())
    if not cleaned:
        return ""
    lower = cleaned.lower()
    if lower.startswith("speaker_") or lower in {"unknown", "speaker a", "speaker b", "speaker c"}:
        return ""
    return cleaned.split()[0]


def duration_from_transcript(path: Path) -> tuple[str, float]:
    data = json.loads(path.read_text(errors="replace"))
    segments = [s for s in data.get("segments") or [] if isinstance(s, dict)]
    duration = 0.0
    texts: list[tuple[float, str]] = []
    for segment in segments:
        text = " ".join(str(segment.get("text") or "").split())
        start = float(segment.get("start") or 0)
        end = segment.get("end")
        if isinstance(end, (int, float)):
            duration = max(duration, float(end))
        if text:
            texts.append((start, text))
    return sample_text(texts), duration


def sample_text(texts: list[tuple[float, str]], max_chars: int = 9000) -> str:
    if not texts:
        return ""
    total = sum(len(text) + 1 for _, text in texts)
    if total <= max_chars:
        return "\n".join(f"[{format_clock(start)}] {text}" for start, text in texts)

    buckets = [(0.0, 0.2), (0.4, 0.6), (0.8, 1.0)]
    selected: list[str] = []
    n = len(texts)
    per_bucket = max_chars // len(buckets)
    for lo, hi in buckets:
        start_i = int(n * lo)
        end_i = max(start_i + 1, int(n * hi))
        chars = 0
        for start, text in texts[start_i:end_i]:
            line = f"[{format_clock(start)}] {text}"
            if chars + len(line) > per_bucket and chars > 0:
                break
            selected.append(line)
            chars += len(line)
    return "\n".join(selected)[:max_chars]


def format_clock(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def date_prefix_from(title: str, recorded_at: str) -> str:
    match = DATE_PREFIX_RE.match(title or "")
    if match:
        return match.group("prefix")
    if recorded_at:
        raw = recorded_at.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
            return dt.strftime("%y%m%d %H:%M")
        except ValueError:
            pass
    return datetime.now(timezone.utc).strftime("%y%m%d %H:%M")


def existing_location(title: str) -> str:
    match = LOCATION_RE.search(title or "")
    if match:
        return " ".join(match.group("location").strip().split())
    body_match = DATE_PREFIX_RE.match(title or "")
    body = body_match.group("body") if body_match else title
    body = DURATION_RE.sub("", body or "").strip()
    body = SPEAKER_BLOCK_RE.sub("", body).strip()
    if any(body.lower().startswith(prefix) for prefix in LOCATION_PREFIXES):
        return body
    return ""


def duration_label(existing_title: str, duration_seconds: float) -> str:
    match = DURATION_RE.search(existing_title or "")
    if match and duration_seconds <= 0:
        label = match.group("label")
        if len(label) == 4 and ":" not in label:
            return f"{label[:2]}:{label[2:]}"
        return label
    return format_clock(duration_seconds)


def load_candidates(db_path: Path, labels: set[str], limit: int) -> list[Candidate]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, title, recorded_at, created_at, transcript_json_path, summary
        FROM transcription_jobs
        WHERE status = 'completed'
          AND deleted_at IS NULL
          AND (is_source_part = 0 OR is_source_part IS NULL)
          AND transcript_json_path IS NOT NULL
          AND transcript_json_path != ''
        ORDER BY recorded_at DESC, created_at DESC
        """
    ).fetchall()

    out: list[Candidate] = []
    for row in rows:
        row_labels = classify(row["title"] or "")
        if labels and not labels.intersection(row_labels):
            continue
        path = Path(row["transcript_json_path"] or "")
        if not path.exists():
            continue
        _, duration = duration_from_transcript(path)
        speakers = [
            first_name(r["custom_name"])
            for r in conn.execute(
                "SELECT custom_name FROM speaker_mappings WHERE transcription_job_id = ? ORDER BY original_speaker",
                (row["id"],),
            ).fetchall()
        ]
        speakers = list(dict.fromkeys([s for s in speakers if s]))
        out.append(
            Candidate(
                id=row["id"],
                title=row["title"] or "",
                recorded_at=row["recorded_at"] or row["created_at"] or "",
                transcript_json_path=row["transcript_json_path"] or "",
                summary=row["summary"] or "",
                labels=row_labels,
                duration_seconds=duration,
                speakers=speakers,
            )
        )
        if limit and len(out) >= limit:
            break
    return out


def ollama_title(ollama_base: str, model: str, candidate: Candidate, sampled_text: str) -> str:
    prompt = f"""Create a concise subject title for this personal voice memo transcript.

Rules:
- Return only the subject title, 3-8 words.
- Title Case.
- Be specific and concrete.
- Do not include date/time, duration, speakers, brackets, @location, or file names.
- Do not use generic labels such as Brief Note, Personal Issue, Conversation, Discussion, Chat, Recording.
- Do not use filler such as Thank You.

Current bad title: {candidate.title}
Existing summary, if any:
{candidate.summary[:1200]}

Transcript sample:
{sampled_text}
"""
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{ollama_base.rstrip('/')}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    return normalize_subject(payload.get("response") or "")


def normalize_subject(value: str) -> str:
    subject = " ".join((value or "").replace("\n", " ").split())
    subject = subject.strip(" `\"'.:;-")
    subject = re.sub(r"^(Title|Subject)\s*:\s*", "", subject, flags=re.IGNORECASE).strip()
    if len(subject) > 70:
        subject = subject[:67].rstrip() + "..."
    return subject


def weak_subject(subject: str) -> bool:
    lower = subject.lower().strip()
    if not lower:
        return True
    if "thank you" in lower or FILENAME_LIKE_RE.search(subject):
        return True
    if lower in GENERIC_TERMS or any(lower.startswith(term + " ") for term in GENERIC_TERMS):
        return True
    if any(lower.startswith(prefix) for prefix in LOCATION_PREFIXES):
        return True
    return False


def build_title(candidate: Candidate, subject: str) -> str:
    parts = [date_prefix_from(candidate.title, candidate.recorded_at), subject]
    if candidate.speakers:
        parts.append(f"[{', '.join(candidate.speakers)}]")
    location = existing_location(candidate.title)
    if location:
        parts.append(f"@{location}")
    parts.append(f"{{{duration_label(candidate.title, candidate.duration_seconds)}}}")
    return " ".join(parts)


def update_title(api_base: str, job_id: str, title: str) -> tuple[bool, str]:
    body = json.dumps({"title": title}).encode("utf-8")
    request = urllib.request.Request(
        f"{api_base.rstrip('/')}/api/v1/transcription/{job_id}/title",
        data=body,
        headers={"Content-Type": "application/json"},
        method="PUT",
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
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--ollama-base", default=DEFAULT_OLLAMA_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--labels", default="raw_apple_filename,location_only,thank_you_title")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    labels = {label.strip() for label in args.labels.split(",") if label.strip()}
    candidates = load_candidates(Path(args.db).expanduser(), labels, args.limit)
    print("job_id\tlabels\told_title\tnew_title\tapplied")
    for candidate in candidates:
        sampled_text, _ = duration_from_transcript(Path(candidate.transcript_json_path))
        subject = ollama_title(args.ollama_base, args.model, candidate, sampled_text)
        if weak_subject(subject):
            subject = normalize_subject(DATE_PREFIX_RE.sub(r"\g<body>", candidate.title))
        new_title = build_title(candidate, subject)
        applied = False
        if args.apply and new_title != candidate.title:
            ok, response = update_title(args.api_base, candidate.id, new_title)
            applied = ok
            if not ok:
                print(f"# update failed for {candidate.id}: {response[:500]}", file=sys.stderr)
        print(f"{candidate.id}\t{','.join(candidate.labels)}\t{candidate.title}\t{new_title}\t{applied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
