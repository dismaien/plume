#!/usr/bin/env python3
"""Reference-free decoder timing evidence for word-speaker evaluation.

This module never edits transcript text or accepted timestamps. It can emit an
evaluation-only speaker proposal when an inflated decoder word contains two
separated activity islands and the trailing island belongs to an otherwise
unmapped native channel. Such proposals are identity hypotheses and always
require review; they are not safe production labels.
"""

from copy import deepcopy
from difflib import SequenceMatcher
import math
from pathlib import Path
import unicodedata


POLICY = {
    "aligned_decoder_ratio_max": 0.60,
    "minimum_saved_seconds": 0.40,
    "maximum_decoder_probability": 0.50,
    "minimum_activity": 0.90,
    "minimum_activity_margin": 0.50,
    "minimum_trailing_island_seconds": 0.08,
    "minimum_internal_gap_seconds": 0.20,
    "boundary_tolerance_seconds": 0.03,
    "version": "decoder_trailing_activity_island_v1_evaluation_only",
}


def normalize_word(value):
    value = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(c for c in value if not unicodedata.category(c).startswith("P")).strip()


def flatten_decoder_words(payload):
    return [word for segment in payload.get("segments", []) for word in segment.get("words", [])]


def matched_decoder_words(aligned_words, decoder_words):
    """Order-preserving exact-token matches; duplicate words stay deterministic."""
    aligned = [normalize_word(word.get("word", "")) for word in aligned_words]
    decoded = [normalize_word(word.get("word", "")) for word in decoder_words]
    matches = {}
    for block in SequenceMatcher(a=aligned, b=decoded, autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            if aligned[block.a + offset]:
                matches[block.a + offset] = block.b + offset
    return matches


def _activity(payload):
    import hashlib
    import numpy as np
    from scipy.special import expit

    artifact = payload["activity_artifact"]
    path = Path(artifact["path"])
    with path.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != artifact["sha256"]:
            raise ValueError("Native frame artifact changed")
    with np.load(path, allow_pickle=False) as data:
        logits = data["logits"]
        mask = data["attention_mask"].astype(bool)
        step = float(data["frame_seconds"])
        return expit(logits), mask, step


def _strong_runs(probabilities, mask, step, start, end):
    import numpy as np

    left = max(0, int(math.floor(start / step)))
    right = min(len(mask), max(left + 1, int(math.ceil(end / step))))
    runs = []
    for frame in range(left, right):
        values = probabilities[frame]
        order = np.argsort(values)
        winner = int(order[-1])
        activity = float(values[winner])
        margin = float(values[order[-1]] - values[order[-2]]) if len(order) > 1 else activity
        strong = bool(mask[frame] and activity >= POLICY["minimum_activity"]
                      and margin >= POLICY["minimum_activity_margin"])
        frame_start, frame_end = max(start, frame * step), min(end, (frame + 1) * step)
        if not strong or frame_end <= frame_start:
            continue
        if runs and runs[-1]["channel"] == winner and frame_start <= runs[-1]["end"] + step / 2:
            run = runs[-1]
            run["end"] = frame_end
            run["activity"] = max(run["activity"], activity)
            run["margin"] = max(run["margin"], margin)
        else:
            runs.append({"start": frame_start, "end": frame_end, "channel": winner,
                         "activity": activity, "margin": margin})
    return runs


def timing_evidence(aligned_words, decoder_words, native):
    """Return narrow acoustic windows without reading any reference labels."""
    probabilities, mask, step = _activity(native)
    matches = matched_decoder_words(aligned_words, decoder_words)
    evidence = []
    for aligned_index, decoder_index in matches.items():
        aligned_word = aligned_words[aligned_index]
        decoded_word = decoder_words[decoder_index]
        start, end = float(aligned_word["start"]), float(aligned_word["end"])
        decoder_start, decoder_end = float(decoded_word["start"]), float(decoded_word["end"])
        aligned_duration, decoder_duration = end - start, decoder_end - decoder_start
        if (aligned_duration <= 0 or decoder_duration <= 0
                or decoder_duration > aligned_duration * POLICY["aligned_decoder_ratio_max"]
                or aligned_duration - decoder_duration < POLICY["minimum_saved_seconds"]
                or float(decoded_word.get("probability", 1.0)) > POLICY["maximum_decoder_probability"]):
            continue
        if decoder_index == 0:
            continue
        previous_end = float(decoder_words[decoder_index - 1]["end"])
        if abs(previous_end - decoder_start) > POLICY["boundary_tolerance_seconds"]:
            continue
        runs = _strong_runs(probabilities, mask, step, decoder_start, decoder_end)
        if len(runs) < 2:
            continue
        trailing = runs[-1]
        previous = runs[-2]
        if (decoder_end - trailing["end"] > POLICY["boundary_tolerance_seconds"]
                or trailing["end"] - trailing["start"] < POLICY["minimum_trailing_island_seconds"]
                or trailing["start"] - previous["end"] < POLICY["minimum_internal_gap_seconds"]
                or trailing["channel"] == previous["channel"]):
            continue
        evidence.append({
            "index": aligned_index,
            "word": aligned_word["word"],
            "aligned_start": start,
            "aligned_end": end,
            "decoder_start": decoder_start,
            "decoder_end": decoder_end,
            "evidence_start": trailing["start"],
            "evidence_end": trailing["end"],
            "native_channel": f"speaker_{trailing['channel']}",
            "activity": trailing["activity"],
            "margin": trailing["margin"],
            "decoder_probability": float(decoded_word.get("probability", 0.0)),
        })
    return evidence


def build_evaluation_proposals(primary, native, words, decoder_words):
    """Wrap the accepted known-label policy and add review-only hypotheses."""
    from word_rescue import propose

    original = deepcopy(words)
    result = propose(primary, native, words)
    diagnostics = timing_evidence(words, decoder_words, native) if decoder_words else []
    existing = {proposal["index"] for proposal in result["proposals"]}
    labels = {str(turn["speaker"]) for turn in primary.get("regular_segments", primary.get("segments", []))
              if turn.get("speaker")}
    used = {int(label.split("_")[-1]) for label in labels
            if label.startswith("speaker_") and label.split("_")[-1].isdigit()}
    next_id = max(used, default=-1) + 1
    for item in diagnostics:
        index = item["index"]
        current = words[index].get("speaker")
        proposed = result["mapping"].get(item["native_channel"])
        if index in existing or not current or str(current).strip().lower() in ("unknown", "speaker unclear"):
            item["outcome"] = "diagnostic_only_unknown_or_already_proposed"
            continue
        if proposed == current:
            item["outcome"] = "same_mapped_identity"
            continue
        if proposed is None:
            proposed = f"speaker_{next_id}"
            next_id += 1
            item["identity_evidence"] = "unmapped_native_channel; anonymous hypothesis only"
        result["proposals"].append({
            "index": index,
            "start": float(words[index]["start"]),
            "end": float(words[index]["end"]),
            "word": words[index]["word"],
            "previous": current,
            "proposed": proposed,
            "activity": item["activity"],
            "margin": item["margin"],
        })
        item.update(outcome="evaluation_only_identity_hypothesis", previous=current, proposed=proposed)
    if words != original:
        raise AssertionError("Timing evaluation mutated transcript words")
    result.update(alignment_policy=POLICY, alignment_diagnostics=diagnostics,
                  safety="evaluation-only hypotheses; accepted text/timestamps/names unchanged")
    return result
