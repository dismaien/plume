#!/usr/bin/env python3
"""Build a self-contained interactive quality audit for the Plume archive.

The audit is read-only. It combines transcript repetition signals, speaker
diarization evidence, metadata consistency, file availability, and processing
settings for every non-deleted parent recording in the Plume database.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_DB = Path.home() / "Library/Application Support/plume-desktop/data/quill.db"
DEFAULT_OUTPUT = (
    Path.home()
    / "Documents/Voice Notes Archive/20-quill-processing/archive-quality-audit.html"
)

SEVERITY_RANK = {
    "Critical": 4,
    "High": 3,
    "Review": 2,
    "No automated flag": 1,
    "Unavailable": 0,
}


@dataclass
class AuditRow:
    job_id: str
    title: str
    status: str
    recorded_at: str
    duration_seconds: float
    model_family: str
    model: str
    diarize_model: str
    latest_vad_method: str
    latest_max_speakers: int | None
    latest_execution_status: str
    word_count: int
    words_per_minute: float
    text_score: int
    legacy_repetition_score: int
    thank_you_count: int
    big_big_count: int
    repeated_word_runs: int
    repeated_phrase_runs: int
    max_repeated_tokens: int
    top_repeated_phrase: str
    top_repeated_count: int
    suspicious_thank_you_segments: int
    speaker_count: int
    known_people_count: int
    known_people: str
    dominant_speaker_ratio: float
    segment_switches: int
    word_switches: int
    switches_per_hour: float
    unlabeled_speech_ratio: float
    title_duration_seconds: float | None
    title_duration_delta_seconds: float | None
    audio_exists: bool
    audio_size_bytes: int
    transcript_exists: bool
    transcript_size_bytes: int
    transcript_path: str
    audio_path: str
    original_filename: str
    human_speaker_mappings: int
    severity: str
    risk_score: int
    categories: str
    flags: str
    evidence: str
    recommended_action: str
    record_type: str = "audio"
    suspicious_filler_segments: int = 0
    repair_state: str = "not_attempted"
    repair_attempts: int = 0
    repair_reason: str = ""


def words_from(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z']+", text.lower())


def repeated_word_runs(words: list[str], min_run: int = 8) -> int:
    if not words:
        return 0
    runs = 0
    current = 1
    for previous, word in zip(words, words[1:]):
        if word == previous:
            current += 1
        else:
            if current >= min_run:
                runs += 1
            current = 1
    return runs + int(current >= min_run)


def repeated_phrase_metrics(words: list[str]) -> tuple[int, int, str, int]:
    """Return run count, largest repeated span, phrase, and repeat count."""
    findings: list[tuple[int, int, str, int]] = []
    thresholds = {1: 6, 2: 4, 3: 3, 4: 3, 5: 3, 6: 3}
    index = 0
    while index < len(words):
        candidates: list[tuple[int, int, str, int]] = []
        for size, minimum in thresholds.items():
            if index + size * minimum > len(words):
                continue
            phrase = words[index : index + size]
            repeats = 1
            while (
                index + size * (repeats + 1) <= len(words)
                and words[index + size * repeats : index + size * (repeats + 1)] == phrase
            ):
                repeats += 1
            if repeats >= minimum:
                token_span = size * repeats
                candidates.append((token_span, size, " ".join(phrase), repeats))
        if not candidates:
            index += 1
            continue
        best = max(candidates, key=lambda finding: (finding[0], -finding[1]))
        findings.append(best)
        index += best[0]
    if not findings:
        return 0, 0, "", 0
    findings.sort(key=lambda finding: (finding[0], -finding[1]), reverse=True)
    largest = findings[0]
    return len(findings), largest[0], largest[2], largest[3]


def title_people(title: str) -> set[str]:
    people: set[str] = set()
    for contents in re.findall(r"\[([^\]]+)\]", title):
        for value in contents.split(","):
            name = value.strip()
            if name and not re.fullmatch(r"speaker\s+[a-z0-9]+", name, re.I):
                people.add(name)
    return people


def trusted_people(conn: sqlite3.Connection) -> tuple[dict[str, set[str]], dict[str, int]]:
    result: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    rows = conn.execute(
        """
        SELECT transcription_job_id, original_speaker, custom_name,
               match_source, review_status
        FROM speaker_mappings
        """
    ).fetchall()
    for row in rows:
        job_id = str(row["transcription_job_id"])
        name = str(row["custom_name"] or "").strip()
        original = str(row["original_speaker"] or "").strip()
        source = str(row["match_source"] or "").strip()
        review = str(row["review_status"] or "").strip()
        trusted = source == "manual" or review in {"accepted", "corrected"}
        trusted = trusted or (not source and name and name != original)
        if trusted and name:
            result.setdefault(job_id, set()).add(name)
            counts[job_id] = counts.get(job_id, 0) + 1
    return result, counts


def latest_executions(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT e.transcription_job_id, e.status, e.actual_vad_method,
               e.actual_max_speakers, e.created_at
        FROM transcription_job_executions e
        JOIN (
            SELECT transcription_job_id, MAX(created_at) AS latest_created
            FROM transcription_job_executions
            GROUP BY transcription_job_id
        ) latest
          ON latest.transcription_job_id = e.transcription_job_id
         AND latest.latest_created = e.created_at
        """
    ).fetchall()
    return {str(row["transcription_job_id"]): row for row in rows}


def parse_title_duration(title: str) -> float | None:
    matches = re.findall(r"\{([^{}]+)\}", title)
    if not matches:
        return None
    value = matches[-1].strip()
    if re.fullmatch(r"\d{4}", value):
        return int(value[:2]) * 60 + int(value[2:])
    if re.fullmatch(r"\d{6}", value):
        return int(value[:2]) * 3600 + int(value[2:4]) * 60 + int(value[4:])
    parts = value.split(":")
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return None


def safe_size(path_text: str) -> tuple[bool, int]:
    if not path_text:
        return False, 0
    path = Path(path_text)
    try:
        return path.is_file(), path.stat().st_size if path.is_file() else 0
    except OSError:
        return False, 0


def parse_transcript(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise ValueError("Transcript must contain a segments list")
    segments = payload["segments"]
    intervals = []
    invalid_timing_count = 0
    text_parts: list[str] = []
    duration = 0.0
    speaker_durations: Counter[str] = Counter()
    segment_switches = 0
    previous_speaker = ""
    word_switches = 0
    previous_word_speaker = ""
    unlabeled_duration = 0.0
    suspicious_thanks = 0
    suspicious_fillers = 0
    evidence: list[str] = []

    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("Invalid transcript segment")
        text = str(segment.get("text") or "").strip()
        if text:
            text_parts.append(text)
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("Nonfinite segment timing")
        if start < 0 or end < start:
            invalid_timing_count += 1
        if text and start >= 0 and end >= start:
            intervals.append((start, end))
        segment_duration = max(0.0, end - start)
        duration = max(duration, end)
        speaker = str(segment.get("speaker") or "").strip()
        if speaker:
            speaker_durations[speaker] += segment_duration
            if previous_speaker and previous_speaker != speaker:
                segment_switches += 1
            previous_speaker = speaker
        else:
            unlabeled_duration += segment_duration

        isolated_thanks = re.fullmatch(r"(?:\.\.\.\s*)?thank you[.!?]*", text, re.I)
        isolated_filler = isolated_thanks or re.fullmatch(r"(?:\.\.\.\s*)?(?:vielen dank|danke(?: schön)?)[.!?]*", text, re.I)
        if isolated_filler:
            if segment_duration < 0.45 or segment_duration > 4.0:
                suspicious_fillers += 1
                suspicious_thanks += int(bool(isolated_thanks))
                if len(evidence) < 6:
                    evidence.append(f"{start / 60:.2f}m ({segment_duration:.2f}s): {text}")

        for word in segment.get("words") or []:
            if not isinstance(word, dict):
                continue
            word_speaker = str(word.get("speaker") or "").strip()
            if not word_speaker:
                continue
            if previous_word_speaker and previous_word_speaker != word_speaker:
                word_switches += 1
            previous_word_speaker = word_speaker

    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    speech_seconds = sum(end - start for start, end in merged)
    text = " ".join(text_parts)
    words = words_from(text)
    lower = text.lower()
    thank_you = len(re.findall(r"\bthank you\b", lower))
    big_big = len(re.findall(r"\bbig big\b", lower))
    word_runs = repeated_word_runs(words)
    phrase_runs, max_repeated_tokens, repeated_phrase, repeated_count = repeated_phrase_metrics(words)
    legacy_score = thank_you * 2 + big_big * 3 + word_runs * 10
    text_score = min(
        100,
        legacy_score
        + phrase_runs * 5
        + max(0, max_repeated_tokens - 6) * 2
        + suspicious_fillers * 4,
    )
    total_speech = sum(speaker_durations.values())
    dominant_ratio = max(speaker_durations.values(), default=0.0) / total_speech if total_speech else 0.0
    unlabeled_ratio = unlabeled_duration / max(duration, 1.0)
    return {
        "duration": duration,
        "speech_seconds": speech_seconds,
        "invalid_timing_count": invalid_timing_count,
        "speech_intervals": merged,
        "word_count": len(words),
        "words_per_minute": len(words) / (duration / 60) if duration else 0.0,
        "text_score": text_score,
        "legacy_score": legacy_score,
        "thank_you": thank_you,
        "big_big": big_big,
        "word_runs": word_runs,
        "phrase_runs": phrase_runs,
        "max_repeated_tokens": max_repeated_tokens,
        "top_repeated_phrase": repeated_phrase,
        "top_repeated_count": repeated_count,
        "suspicious_thanks": suspicious_thanks,
        "suspicious_fillers": suspicious_fillers,
        "speaker_count": len(speaker_durations),
        "dominant_ratio": dominant_ratio,
        "segment_switches": segment_switches,
        "word_switches": word_switches,
        "switches_per_hour": segment_switches / (duration / 3600) if duration else 0.0,
        "unlabeled_ratio": unlabeled_ratio,
        "evidence": evidence,
    }


def classify(
    job: sqlite3.Row,
    metrics: dict[str, Any] | None,
    known_people: set[str],
    audio_exists: bool,
    transcript_exists: bool,
    title_delta: float | None,
) -> tuple[str, int, list[str], list[str], str]:
    status = str(job["status"] or "")
    flags: list[str] = []
    categories: set[str] = set()
    risk = 0

    if status == "failed":
        flags.append("Transcription job failed")
        categories.add("availability")
        return "Unavailable", 100, sorted(categories), flags, "Retry after checking the recorded error."
    if status != "completed":
        flags.append(f"No completed transcript ({status or 'unknown status'})")
        categories.add("availability")
        return "Unavailable", 0, sorted(categories), flags, "Transcribe this recording before judging quality."
    if not transcript_exists or metrics is None:
        flags.append("Completed job has no readable transcript JSON")
        categories.add("files")
        risk = 100
    if not audio_exists:
        flags.append("Audio file is missing from the recorded path")
        categories.add("files")
        risk = max(risk, 90)
    if metrics is None:
        return "Critical", risk, sorted(categories), flags, "Restore the missing artifact before retranscribing."

    if not metrics["word_count"]:
        flags.append("Completed transcript contains no words")
        categories.add("text")
        risk = max(risk, 48)
    if metrics.get("invalid_timing_count"):
        flags.append(f"{metrics['invalid_timing_count']} segment(s) with invalid timing")
        categories.add("files")
        risk = max(risk, 48)

    legacy = int(metrics["legacy_score"])
    max_span = int(metrics["max_repeated_tokens"])
    if legacy >= 250 or max_span >= 80:
        flags.append(f"Catastrophic repetition signal (score {legacy}, max span {max_span} words)")
        categories.add("text")
        risk = max(risk, 100)
    elif legacy >= 50 or max_span >= 24:
        flags.append(f"Strong repetition signal (score {legacy}, max span {max_span} words)")
        categories.add("text")
        risk = max(risk, 78)
    elif legacy >= 12 or max_span >= 8:
        flags.append(f"Repetition needs review (score {legacy}, max span {max_span} words)")
        categories.add("text")
        risk = max(risk, 48)
    if metrics.get("suspicious_fillers", metrics["suspicious_thanks"]):
        flags.append(f"{metrics.get('suspicious_fillers', metrics['suspicious_thanks'])} implausibly timed isolated filler segment(s)")
        categories.add("text")
        risk = max(risk, 55)

    known_count = len(known_people)
    speaker_count = int(metrics["speaker_count"])
    dominant = float(metrics["dominant_ratio"])
    duration = float(metrics["duration"])
    switches_per_hour = float(metrics["switches_per_hour"])
    diarizer = str(job["diarize_model"] or "")
    if duration >= 120 and known_count >= 2 and speaker_count < 2:
        flags.append(f"Known multi-person recording has only {speaker_count} detected speaker")
        categories.add("speakers")
        risk = max(risk, 100)
    elif duration >= 300 and known_count >= 2 and dominant >= 0.98:
        flags.append(f"One speaker owns {dominant:.1%} of labeled speech")
        categories.add("speakers")
        risk = max(risk, 95)
    elif duration >= 300 and known_count >= 2 and dominant >= 0.95:
        flags.append(f"Probable speaker collapse: dominant share {dominant:.1%}")
        categories.add("speakers")
        risk = max(risk, 80)
    elif duration >= 300 and known_count >= 2 and dominant >= 0.90:
        flags.append(f"Speaker balance needs review: dominant share {dominant:.1%}")
        categories.add("speakers")
        risk = max(risk, 52)
    if (
        diarizer in {"sherpa-onnx", "sherpa_onnx"}
        and duration >= 300
        and speaker_count >= 2
        and dominant >= 0.95
        and switches_per_hour < 20
    ):
        flags.append("Matches the sherpa-onnx collapse signature")
        categories.add("speakers")
        risk = max(risk, 82)
    if duration >= 300 and switches_per_hour > 500:
        flags.append(f"Possible over-segmentation ({switches_per_hour:.0f} switches/hour)")
        categories.add("speakers")
        risk = max(risk, 45)

    if title_delta is not None:
        threshold = max(60.0, duration * 0.1)
        if title_delta > threshold:
            flags.append(f"Title duration differs by {title_delta / 60:.1f} minutes")
            categories.add("metadata")
            risk = max(risk, 45 if title_delta < 600 else 65)
    title = str(job["title"] or "")
    if re.search(r"\b20\d{6}\s+\d{6}-[0-9A-F]{8}\b", title, re.I):
        flags.append("Title still contains a generated filename identifier")
        categories.add("metadata")
        risk = max(risk, 35)

    if risk >= 90:
        severity = "Critical"
    elif risk >= 65:
        severity = "High"
    elif risk > 0:
        severity = "Review"
    else:
        severity = "No automated flag"

    if "files" in categories:
        action = "Restore the missing source or transcript artifact first."
    elif risk >= 65 and "text" in categories and "speakers" in categories:
        action = "High-quality retranscription plus diarization review."
    elif risk >= 65 and "text" in categories:
        action = "Rerun with WhisperX large-v3 and review suspicious spans."
    elif risk >= 65 and "speakers" in categories:
        action = "Rerun with Sortformer and an evidence-based speaker limit."
    elif risk > 0:
        action = "Inspect the flagged evidence before replacing the transcript."
    else:
        action = "No action suggested by automated checks."
    return severity, risk, sorted(categories), flags, action


def scan(db_path: Path) -> tuple[list[AuditRow], dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    people_by_job, mapping_counts = trusted_people(conn)
    executions = latest_executions(conn)
    jobs = conn.execute(
        """
        SELECT id, title, status, audio_path, transcript_json_path,
               transcript_markdown_path, model_family, model, diarize_model,
               diarize, recorded_at, created_at, updated_at, original_filename
        FROM transcription_jobs
        WHERE deleted_at IS NULL
          AND (is_source_part = 0 OR is_source_part IS NULL)
        ORDER BY COALESCE(recorded_at, created_at) DESC
        """
    ).fetchall()

    rows: list[AuditRow] = []
    parse_errors = 0
    for job in jobs:
        job_id = str(job["id"])
        transcript_path = str(job["transcript_json_path"] or "")
        audio_path = str(job["audio_path"] or "")
        transcript_exists, transcript_size = safe_size(transcript_path)
        audio_exists, audio_size = safe_size(audio_path)
        metrics: dict[str, Any] | None = None
        reference_document = False
        if transcript_exists:
            try:
                payload = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
                reference_document = isinstance(payload, dict) and payload.get("source") == "granola" and "chunks" in payload
                if not reference_document:
                    metrics = parse_transcript(Path(transcript_path))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                parse_errors += 1

        known = set(people_by_job.get(job_id, set()))
        known.update(title_people(str(job["title"] or "")))
        title_duration = parse_title_duration(str(job["title"] or ""))
        actual_duration = float(metrics["duration"] if metrics else 0.0)
        title_delta = (
            abs(title_duration - actual_duration)
            if title_duration is not None and actual_duration > 0
            else None
        )
        severity, risk, categories, flags, action = classify(
            job,
            metrics,
            known,
            audio_exists,
            transcript_exists,
            title_delta,
        )
        if reference_document:
            severity, risk, categories = "Unavailable", 0, ["availability"]
            flags = ["Reference-only Granola document; no source audio attached"]
            action = "Retain imported document. Audio repair is not applicable without a source recording."
        current = executions.get(job_id)
        evidence = list(metrics["evidence"] if metrics else [])
        if metrics and metrics["top_repeated_phrase"]:
            evidence.insert(
                0,
                f"Repeated phrase: '{metrics['top_repeated_phrase']}' x{metrics['top_repeated_count']}",
            )
        rows.append(
            AuditRow(
                job_id=job_id,
                title=str(job["title"] or "Untitled recording"),
                status=str(job["status"] or ""),
                recorded_at=str(job["recorded_at"] or job["created_at"] or ""),
                duration_seconds=round(actual_duration, 3),
                model_family=str(job["model_family"] or ""),
                model=str(job["model"] or ""),
                diarize_model=str(job["diarize_model"] or ""),
                latest_vad_method=str(current["actual_vad_method"] or "") if current else "",
                latest_max_speakers=int(current["actual_max_speakers"]) if current and current["actual_max_speakers"] is not None else None,
                latest_execution_status=str(current["status"] or "") if current else "",
                word_count=int(metrics["word_count"] if metrics else 0),
                words_per_minute=round(float(metrics["words_per_minute"] if metrics else 0.0), 2),
                text_score=int(metrics["text_score"] if metrics else 0),
                legacy_repetition_score=int(metrics["legacy_score"] if metrics else 0),
                thank_you_count=int(metrics["thank_you"] if metrics else 0),
                big_big_count=int(metrics["big_big"] if metrics else 0),
                repeated_word_runs=int(metrics["word_runs"] if metrics else 0),
                repeated_phrase_runs=int(metrics["phrase_runs"] if metrics else 0),
                max_repeated_tokens=int(metrics["max_repeated_tokens"] if metrics else 0),
                top_repeated_phrase=str(metrics["top_repeated_phrase"] if metrics else ""),
                top_repeated_count=int(metrics["top_repeated_count"] if metrics else 0),
                suspicious_thank_you_segments=int(metrics["suspicious_thanks"] if metrics else 0),
                speaker_count=int(metrics["speaker_count"] if metrics else 0),
                known_people_count=len(known),
                known_people=", ".join(sorted(known)),
                dominant_speaker_ratio=round(float(metrics["dominant_ratio"] if metrics else 0.0), 4),
                segment_switches=int(metrics["segment_switches"] if metrics else 0),
                word_switches=int(metrics["word_switches"] if metrics else 0),
                switches_per_hour=round(float(metrics["switches_per_hour"] if metrics else 0.0), 2),
                unlabeled_speech_ratio=round(float(metrics["unlabeled_ratio"] if metrics else 0.0), 4),
                title_duration_seconds=title_duration,
                title_duration_delta_seconds=round(title_delta, 3) if title_delta is not None else None,
                audio_exists=audio_exists,
                audio_size_bytes=audio_size,
                transcript_exists=transcript_exists,
                transcript_size_bytes=transcript_size,
                transcript_path=transcript_path,
                audio_path=audio_path,
                original_filename=str(job["original_filename"] or ""),
                human_speaker_mappings=mapping_counts.get(job_id, 0),
                severity=severity,
                risk_score=risk,
                categories=", ".join(categories),
                flags=" | ".join(flags),
                evidence=" | ".join(evidence),
                recommended_action=action,
                record_type="reference_document" if reference_document else "audio",
                suspicious_filler_segments=int(metrics["suspicious_fillers"] if metrics else 0),
            )
        )

    conn.close()
    rows.sort(
        key=lambda row: (
            SEVERITY_RANK[row.severity],
            row.risk_score,
            row.duration_seconds,
        ),
        reverse=True,
    )
    summary = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "database": str(db_path),
        "total": len(rows),
        "completed": sum(row.status == "completed" for row in rows),
        "failed": sum(row.status == "failed" for row in rows),
        "unavailable": sum(row.severity == "Unavailable" for row in rows),
        "parse_errors": parse_errors,
        "reference_documents": sum(row.record_type == "reference_document" for row in rows),
        "severity_counts": dict(Counter(row.severity for row in rows)),
        "category_counts": dict(
            Counter(category for row in rows for category in row.categories.split(", ") if category)
        ),
        "model_counts": dict(Counter(row.model or "unknown" for row in rows if row.status == "completed")),
        "total_audio_bytes": sum(row.audio_size_bytes for row in rows),
        "total_words": sum(row.word_count for row in rows),
        "total_duration_seconds": sum(row.duration_seconds for row in rows),
    }
    return rows, summary


def write_csv(path: Path, rows: list[AuditRow]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(AuditRow.__dataclass_fields__))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def render_html(rows: list[AuditRow], summary: dict[str, Any]) -> str:
    payload = json.dumps(
        {"summary": summary, "rows": [asdict(row) for row in rows]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    title = "Plume Archive Quality Audit"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: dark; --bg:#07150f; --surface:#0d2117; --surface2:#12291d; --line:#355139; --text:#edf1df; --muted:#9da891; --gold:#d3aa3f; --critical:#ef6a61; --high:#e7a346; --review:#b9ae63; --clean:#68a779; --unavailable:#748176; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; letter-spacing:0; }}
button,input,select {{ font:inherit; letter-spacing:0; }}
.shell {{ max-width:1680px; margin:0 auto; padding:24px; }}
header {{ display:flex; justify-content:space-between; gap:24px; align-items:end; border-bottom:1px solid var(--line); padding:8px 0 20px; }}
h1 {{ margin:0; font-size:28px; font-weight:520; }}
.subtitle {{ color:var(--muted); margin-top:5px; }}
.generated {{ color:var(--muted); text-align:right; font-variant-numeric:tabular-nums; }}
.stats {{ display:grid; grid-template-columns:repeat(8,minmax(105px,1fr)); border-bottom:1px solid var(--line); }}
.stat {{ padding:18px 16px; border-right:1px solid var(--line); }}
.stat:first-child {{ padding-left:0; }} .stat:last-child {{ border-right:0; }}
.stat strong {{ display:block; font-size:25px; font-weight:560; color:var(--gold); }}
.stat span {{ color:var(--muted); font-size:12px; text-transform:uppercase; }}
.overview {{ display:grid; grid-template-columns:1.2fr 1fr; gap:32px; padding:22px 0; border-bottom:1px solid var(--line); }}
.overview h2 {{ font-size:14px; margin:0 0 12px; font-weight:600; }}
.stack {{ height:18px; display:flex; overflow:hidden; border:1px solid var(--line); background:var(--surface); }}
.stack button {{ border:0; min-width:2px; padding:0; cursor:pointer; }}
.legend {{ display:flex; flex-wrap:wrap; gap:14px; margin-top:10px; color:var(--muted); }}
.legend button {{ color:inherit; background:none; border:0; padding:0; cursor:pointer; }}
.dot {{ width:9px; height:9px; display:inline-block; margin-right:5px; }}
.critical {{ background:var(--critical); }} .high {{ background:var(--high); }} .review {{ background:var(--review); }} .clean {{ background:var(--clean); }} .unavailable {{ background:var(--unavailable); }}
.model-bars {{ display:grid; gap:8px; }}
.model-row {{ display:grid; grid-template-columns:minmax(120px,1fr) 2fr 48px; gap:8px; align-items:center; }}
.bar {{ height:8px; background:var(--surface); border:1px solid var(--line); }} .bar i {{ display:block; height:100%; background:var(--gold); }}
.controls {{ position:sticky; top:0; z-index:5; display:grid; grid-template-columns:minmax(220px,2fr) repeat(3,minmax(150px,1fr)) auto; gap:10px; padding:14px 0; background:rgba(7,21,15,.96); border-bottom:1px solid var(--line); }}
input,select,button.control {{ min-height:40px; color:var(--text); background:var(--surface); border:1px solid var(--line); border-radius:5px; padding:0 12px; }}
button.control {{ cursor:pointer; color:var(--gold); }}
.result-line {{ display:flex; justify-content:space-between; gap:16px; color:var(--muted); padding:12px 0 8px; }}
.table-wrap {{ overflow:auto; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }}
table {{ width:100%; border-collapse:collapse; min-width:1240px; }}
th {{ position:sticky; top:0; z-index:3; text-align:left; padding:11px 10px; background:var(--bg); color:var(--muted); border-bottom:1px solid var(--line); font-size:12px; font-weight:600; cursor:pointer; white-space:nowrap; }}
td {{ padding:12px 10px; border-bottom:1px solid #223a29; vertical-align:top; font-variant-numeric:tabular-nums; }}
tbody tr.data-row {{ cursor:pointer; }} tbody tr.data-row:hover {{ background:var(--surface); }}
.sev {{ display:inline-flex; align-items:center; gap:7px; white-space:nowrap; }} .sev .dot {{ margin:0; }}
.title-cell {{ max-width:430px; min-width:300px; }} .title-main {{ color:var(--text); }} .title-meta {{ color:var(--muted); font-size:12px; margin-top:3px; }}
.flags-cell {{ min-width:260px; max-width:420px; color:#d9decd; }}
.muted {{ color:var(--muted); }}
.detail td {{ padding:0; background:#091a12; }} .detail-inner {{ display:grid; grid-template-columns:1.1fr 1fr 1fr; gap:26px; padding:18px 24px 22px; border-bottom:1px solid var(--gold); }}
.detail h3 {{ color:var(--gold); font-size:12px; text-transform:uppercase; margin:0 0 8px; }} .detail p {{ margin:3px 0; }}
.path {{ color:var(--muted); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11px; overflow-wrap:anywhere; }}
.pagination {{ display:flex; align-items:center; justify-content:center; gap:12px; padding:18px 0; }} .pagination button {{ min-width:38px; height:36px; background:var(--surface); color:var(--text); border:1px solid var(--line); border-radius:4px; cursor:pointer; }}
.method {{ margin:16px 0 0; color:var(--muted); font-size:12px; max-width:1050px; }}
@media (max-width:1000px) {{ .stats {{ grid-template-columns:repeat(4,1fr); }} .stat:nth-child(4n) {{ border-right:0; }} .overview {{ grid-template-columns:1fr; }} .controls {{ grid-template-columns:1fr 1fr; }} }}
@media (max-width:600px) {{ .shell {{ padding:14px; }} header {{ align-items:start; flex-direction:column; }} .generated {{ text-align:left; }} .stats {{ grid-template-columns:repeat(2,1fr); }} .stat:nth-child(4n) {{ border-right:1px solid var(--line); }} .stat:nth-child(even) {{ border-right:0; }} .controls {{ position:static; grid-template-columns:1fr; }} .detail-inner {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<main class="shell">
<header><div><h1>{html.escape(title)}</h1><div class="subtitle">Automated evidence across every non-deleted parent recording in Plume</div></div><div class="generated" id="generated"></div></header>
<section class="stats" id="stats"></section>
<section class="overview"><div><h2>Quality classification</h2><div class="stack" id="severityStack"></div><div class="legend" id="severityLegend"></div></div><div><h2>Completed transcripts by model</h2><div class="model-bars" id="modelBars"></div></div></section>
<section class="controls" aria-label="Audit filters">
<input id="search" type="search" placeholder="Search titles, people, flags, filenames">
<select id="severity"><option value="">All classifications</option></select>
<select id="category"><option value="">All signal types</option><option value="text">Text repetition</option><option value="speakers">Speaker recognition</option><option value="metadata">Metadata</option><option value="files">Missing files</option><option value="availability">Availability</option></select>
<select id="model"><option value="">All models</option></select>
<select id="repair"><option value="">All repair states</option></select>
<button class="control" id="export">Export filtered CSV</button>
</section>
<div class="result-line"><span id="resultCount"></span><span>Click a row for evidence and paths</span></div>
<div class="table-wrap"><table><thead><tr>
<th data-sort="severity">Class</th><th data-sort="risk_score">Risk</th><th data-sort="title">Recording</th><th data-sort="duration_seconds">Duration</th><th data-sort="model">Model</th><th data-sort="legacy_repetition_score">Text signal</th><th data-sort="dominant_speaker_ratio">Speakers</th><th data-sort="switches_per_hour">Turns/hour</th><th data-sort="word_count">Words</th><th data-sort="audio_size_bytes">Audio size</th><th data-sort="flags">Evidence</th>
</tr></thead><tbody id="rows"></tbody></table></div>
<div class="pagination"><button id="prev" aria-label="Previous page">&#8592;</button><span id="pageLabel"></span><button id="next" aria-label="Next page">&#8594;</button></div>
<p class="method">This is a conservative triage audit, not a claim that every flag is an error. Named-speaker evidence comes from bracketed titles and trusted manual mappings. Dominance can be legitimate when one person talks more; borderline results therefore stay in Review. “No automated flag” means these checks found no known failure signal, not that every word is correct.</p>
</main>
<script id="audit-data" type="application/json">{payload}</script>
<script>
const audit=JSON.parse(document.getElementById('audit-data').textContent); const all=audit.rows; const s=audit.summary;
const rank={{'Critical':4,'High':3,'Review':2,'No automated flag':1,'Unavailable':0}}; const cls=v=>v==='No automated flag'?'clean':v.toLowerCase();
const fmtDuration=n=>{{n=Math.round(n||0);const h=Math.floor(n/3600),m=Math.floor(n%3600/60),x=n%60;return h?`${{h}}:${{String(m).padStart(2,'0')}}:${{String(x).padStart(2,'0')}}`:`${{m}}:${{String(x).padStart(2,'0')}}`;}};
const fmtBytes=n=>{{if(!n)return '0 B';const u=['B','KB','MB','GB'];let i=0;while(n>=1024&&i<u.length-1){{n/=1024;i++;}}return `${{n.toFixed(i>1?1:0)}} ${{u[i]}}`;}};
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
document.getElementById('generated').textContent=`Generated ${{new Date(s.generated_at).toLocaleString()}}`;
const stats=[['Recordings',s.total.toLocaleString()],['Completed',s.completed.toLocaleString()],['Critical',s.severity_counts.Critical||0],['High',s.severity_counts.High||0],['Review',s.severity_counts.Review||0],['Audio hours',(s.total_duration_seconds/3600).toFixed(1)],['Audio size',fmtBytes(s.total_audio_bytes)],['Total words',s.total_words.toLocaleString()]];
document.getElementById('stats').innerHTML=stats.map(([k,v])=>`<div class="stat"><strong>${{v}}</strong><span>${{k}}</span></div>`).join('');
const severityOrder=['Critical','High','Review','No automated flag','Unavailable'];
document.getElementById('severityStack').innerHTML=severityOrder.map(k=>`<button class="${{cls(k)}}" data-sev="${{k}}" style="width:${{(s.severity_counts[k]||0)/s.total*100}}%" aria-label="${{k}} ${{s.severity_counts[k]||0}}"></button>`).join('');
document.getElementById('severityLegend').innerHTML=severityOrder.map(k=>`<button data-sev="${{k}}"><i class="dot ${{cls(k)}}"></i>${{k}} ${{(s.severity_counts[k]||0).toLocaleString()}}</button>`).join('');
const models=Object.entries(s.model_counts).sort((a,b)=>b[1]-a[1]).slice(0,7), modelMax=Math.max(...models.map(x=>x[1]),1);
document.getElementById('modelBars').innerHTML=models.map(([k,v])=>`<div class="model-row"><span>${{esc(k)}}</span><span class="bar"><i style="width:${{v/modelMax*100}}%"></i></span><span>${{v}}</span></div>`).join('');
const sev=document.getElementById('severity'), model=document.getElementById('model'); severityOrder.forEach(v=>sev.add(new Option(v,v))); [...new Set(all.map(r=>r.model||'unknown'))].sort().forEach(v=>model.add(new Option(v,v)));
const repair=document.getElementById('repair'); [...new Set(all.map(r=>r.repair_state))].sort().forEach(v=>repair.add(new Option(v,v)));
let page=1,pageSize=75,sortKey='risk_score',sortDir=-1,filtered=[];
function apply(){{const q=document.getElementById('search').value.trim().toLowerCase(), sv=sev.value, cat=document.getElementById('category').value, mv=model.value; filtered=all.filter(r=>(!sv||r.severity===sv)&&(!repair.value||r.repair_state===repair.value)&&(!cat||r.categories.split(', ').includes(cat))&&(!mv||(r.model||'unknown')===mv)&&(!q||[r.title,r.known_people,r.flags,r.original_filename,r.job_id,r.repair_state,r.repair_reason].join(' ').toLowerCase().includes(q))); filtered.sort((a,b)=>{{let x=a[sortKey],y=b[sortKey];if(sortKey==='severity'){{x=rank[x];y=rank[y];}} if(typeof x==='string')return x.localeCompare(y)*sortDir; return ((x??0)-(y??0))*sortDir;}}); const pages=Math.max(1,Math.ceil(filtered.length/pageSize));page=Math.min(page,pages);render(pages);}}
function render(pages){{const start=(page-1)*pageSize, slice=filtered.slice(start,start+pageSize), tbody=document.getElementById('rows');tbody.innerHTML=slice.map((r,i)=>{{const id=`d${{start+i}}`;return `<tr class="data-row" data-detail="${{id}}"><td><span class="sev"><i class="dot ${{cls(r.severity)}}"></i>${{esc(r.severity)}}</span></td><td>${{r.risk_score}}</td><td class="title-cell"><div class="title-main">${{esc(r.title)}}</div><div class="title-meta">Repair: ${{esc(r.repair_state)}} · ${{r.repair_attempts}} attempts</div><div class="title-meta">${{esc((r.recorded_at||'').slice(0,16).replace('T',' '))}} · ${{esc(r.known_people||'No trusted names')}}</div></td><td>${{fmtDuration(r.duration_seconds)}}</td><td>${{esc(r.model||'unknown')}}<div class="muted">${{esc(r.diarize_model||'no diarizer')}}</div></td><td>${{r.legacy_repetition_score}}<div class="muted">${{r.thank_you_count}} thanks · ${{r.max_repeated_tokens}} max run</div></td><td>${{r.speaker_count}} detected / ${{r.known_people_count}} known<div class="muted">${{r.dominant_speaker_ratio?Math.round(r.dominant_speaker_ratio*100)+'% dominant':'n/a'}}</div></td><td>${{r.switches_per_hour.toFixed(1)}}</td><td>${{r.word_count.toLocaleString()}}</td><td>${{fmtBytes(r.audio_size_bytes)}}</td><td class="flags-cell">${{esc(r.flags||'No known failure signal')}}</td></tr><tr class="detail" id="${{id}}" hidden><td colspan="11"><div class="detail-inner"><div><h3>Recommended action</h3><p>${{esc(r.recommended_action)}}</p><p class="muted">Categories: ${{esc(r.categories||'none')}}</p><p class="muted">Human speaker mappings: ${{r.human_speaker_mappings}}</p><p class="muted">Repair: ${{esc(r.repair_state)}} — ${{esc(r.repair_reason)}}</p></div><div><h3>Measured evidence</h3><p>${{esc(r.evidence||r.flags||'No additional evidence')}}</p><p class="muted">Word-level switches: ${{r.word_switches}} · Unlabeled speech: ${{(r.unlabeled_speech_ratio*100).toFixed(1)}}%</p><p class="muted">Title duration difference: ${{r.title_duration_delta_seconds==null?'n/a':fmtDuration(r.title_duration_delta_seconds)}}</p></div><div><h3>Files</h3><p class="path">Transcript: ${{esc(r.transcript_path||'missing')}}</p><p class="path">Audio: ${{esc(r.audio_path||'missing')}}</p><p class="path">Job: ${{esc(r.job_id)}}</p></div></div></td></tr>`;}}).join('');document.getElementById('resultCount').textContent=`${{filtered.length.toLocaleString()}} recordings`;document.getElementById('pageLabel').textContent=`Page ${{page}} of ${{pages}}`;document.getElementById('prev').disabled=page<=1;document.getElementById('next').disabled=page>=pages;}}
document.querySelectorAll('input,select').forEach(el=>el.addEventListener('input',()=>{{page=1;apply();}})); document.querySelectorAll('[data-sev]').forEach(el=>el.addEventListener('click',()=>{{sev.value=el.dataset.sev;page=1;apply();}}));
document.querySelectorAll('th[data-sort]').forEach(el=>el.addEventListener('click',()=>{{const k=el.dataset.sort;if(sortKey===k)sortDir*=-1;else{{sortKey=k;sortDir=(k==='title'||k==='model')?1:-1;}}apply();}}));
document.getElementById('rows').addEventListener('click',e=>{{const tr=e.target.closest('tr.data-row');if(!tr)return;const d=document.getElementById(tr.dataset.detail);d.hidden=!d.hidden;}}); document.getElementById('prev').onclick=()=>{{if(page>1){{page--;apply();}}}}; document.getElementById('next').onclick=()=>{{if(page*pageSize<filtered.length){{page++;apply();}}}};
document.getElementById('export').onclick=()=>{{const keys=Object.keys(filtered[0]||{{}});const quote=v=>'"'+String(v??'').replaceAll('"','""')+'"';const csv=[keys.join(','),...filtered.map(r=>keys.map(k=>quote(r[k])).join(','))].join('\\n');const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{{type:'text/csv'}}));a.download='plume-archive-quality-filtered.csv';a.click();URL.revokeObjectURL(a.href);}};
apply();
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--ledger", type=Path, default=Path.home() / "Documents/Voice Notes Archive/20-quill-processing/archive-repair-ledger.sqlite3")
    args = parser.parse_args()

    db_path = args.db.expanduser().resolve()
    output = args.output.expanduser().resolve()
    csv_path = (args.csv or output.with_suffix(".csv")).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    rows, summary = scan(db_path)
    if args.ledger.exists():
        from archive_repair import read_db
        with read_db(args.ledger) as ledger:
            states = {r["job_id"]: dict(r) for r in ledger.execute("SELECT j.*, (SELECT COUNT(*) FROM attempts a WHERE a.job_id=j.job_id) AS attempts FROM jobs j")}
        for row in rows:
            state = states.get(row.job_id, {})
            row.repair_state = state.get("state", "not_attempted")
            row.repair_attempts = state.get("attempts", 0)
            row.repair_reason = state.get("reason", "")
        summary["repair_states"] = dict(Counter(row.repair_state for row in rows))
    output.write_text(render_html(rows, summary), encoding="utf-8")
    write_csv(csv_path, rows)
    print(json.dumps({**summary, "html": str(output), "csv": str(csv_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
