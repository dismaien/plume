#!/usr/bin/env python3
"""Recluster diarization turns by voice identity.

Sortformer's four output heads are activity tracks, not stable speaker
identities. This pass embeds each sufficiently long turn with TitaNet and
clusters the embeddings globally. Weakly separated results fail closed instead
of exposing extra or mixed speaker labels.
"""

from __future__ import annotations

import argparse
import json
import math
import wave

import numpy as np
import torch
from nemo.collections.asr.models import EncDecSpeakerLabelModel
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist, squareform
from sklearn.metrics import silhouette_score


THRESHOLDS = (0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
MIN_TURN_SECONDS = 1.0
MAX_CROP_SECONDS = 20.0
MIN_SILHOUETTE = 0.30


def load_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as handle:
        channels = handle.getnchannels()
        sample_rate = handle.getframerate()
        samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    audio = samples.astype(np.float32).reshape(-1, channels).mean(axis=1) / 32768.0
    return audio, sample_rate


def embed_turns(audio_path: str, turns: list[dict]) -> tuple[list[dict], np.ndarray]:
    model = EncDecSpeakerLabelModel.from_pretrained(model_name="titanet_large", map_location="cpu")
    model.eval()
    audio, sample_rate = load_wav(audio_path)
    kept: list[dict] = []
    embeddings: list[np.ndarray] = []

    with torch.no_grad():
        for turn in turns:
            duration = float(turn["end"]) - float(turn["start"])
            if duration < MIN_TURN_SECONDS:
                continue
            start, end = float(turn["start"]), float(turn["end"])
            if duration > MAX_CROP_SECONDS:
                midpoint = (start + end) / 2.0
                start = midpoint - MAX_CROP_SECONDS / 2.0
                end = midpoint + MAX_CROP_SECONDS / 2.0
            clip = audio[max(0, int(start * sample_rate)):min(len(audio), int(end * sample_rate))]
            if len(clip) < int(sample_rate * 0.5):
                continue
            embedding, _ = model.infer_segment(clip)
            vector = embedding.reshape(-1).cpu().numpy().astype(np.float64)
            if not np.isfinite(vector).all():
                continue
            kept.append(turn)
            embeddings.append(vector)

    if not embeddings:
        return kept, np.empty((0, 0), dtype=np.float64)
    return kept, np.vstack(embeddings)


def choose_clusters(
    embeddings: np.ndarray,
    durations: np.ndarray,
    max_identities: int | None = None,
) -> tuple[np.ndarray | None, dict]:
    if len(embeddings) < 3:
        return None, {"reason": "too_few_embedded_turns"}

    normalized = embeddings / np.clip(np.linalg.norm(embeddings, axis=1, keepdims=True), 1e-9, None)
    distances = pdist(normalized, metric="cosine")
    if not np.isfinite(distances).all() or np.max(distances, initial=0.0) <= 1e-9:
        return None, {"reason": "no_voice_separation"}

    tree = linkage(distances, method="average")
    matrix = squareform(distances)
    sweep: list[dict] = []
    best: tuple[float, float, np.ndarray] | None = None

    for threshold in THRESHOLDS:
        labels = fcluster(tree, t=threshold, criterion="distance")
        count = len(set(labels.tolist()))
        if max_identities and count > max_identities:
            continue
        silhouette = None
        if 1 < count < len(labels):
            silhouette = float(silhouette_score(matrix, labels, metric="precomputed"))
            if best is None or silhouette > best[0]:
                best = (silhouette, threshold, labels)
        sweep.append({"threshold": threshold, "clusters": count, "silhouette": silhouette})

    if best is None:
        return None, {"reason": "no_valid_cluster_solution", "sweep": sweep}
    return best[2], {
        "threshold": best[1],
        "silhouette": best[0],
        "raw_cluster_count": len(set(best[2].tolist())),
        "sweep": sweep,
        "normalized_embeddings": normalized,
    }


def recluster(
    all_turns: list[dict],
    embedded_turns: list[dict],
    embeddings: np.ndarray,
    max_identities: int | None = None,
    min_silhouette: float = MIN_SILHOUETTE,
) -> dict:
    durations = np.array(
        [float(turn["end"]) - float(turn["start"]) for turn in embedded_turns],
        dtype=np.float64,
    )
    labels, diagnostics = choose_clusters(embeddings, durations, max_identities)
    diagnostics.pop("normalized_embeddings", None)
    if labels is None:
        return {"status": "uncertain", "segments": [], **diagnostics}

    silhouette = float(diagnostics["silhouette"])
    if silhouette < min_silhouette:
        return {
            "status": "uncertain",
            "reason": "weak_voice_separation",
            "segments": [],
            **diagnostics,
        }

    normalized = embeddings / np.clip(np.linalg.norm(embeddings, axis=1, keepdims=True), 1e-9, None)
    total_speech = float(durations.sum())
    seconds_by_raw_cluster: dict[int, float] = {}
    for label, duration in zip(labels, durations):
        seconds_by_raw_cluster[int(label)] = seconds_by_raw_cluster.get(int(label), 0.0) + float(duration)

    min_identity_seconds = max(20.0, 0.03 * total_speech)
    identity_labels = [
        label
        for label, seconds in sorted(seconds_by_raw_cluster.items(), key=lambda item: -item[1])
        if seconds >= min_identity_seconds
    ]
    if max_identities:
        identity_labels = identity_labels[:max_identities]
    if not identity_labels:
        identity_labels = [max(seconds_by_raw_cluster, key=seconds_by_raw_cluster.get)]

    centroids: dict[int, np.ndarray] = {}
    for label in identity_labels:
        indexes = np.where(labels == label)[0]
        centroid = (normalized[indexes] * durations[indexes, None]).sum(axis=0)
        centroids[label] = centroid / max(float(np.linalg.norm(centroid)), 1e-9)

    assigned: list[dict] = []
    raw_head_votes: dict[str, dict[int, float]] = {}
    for index, turn in enumerate(embedded_turns):
        ranked = sorted((1.0 - float(normalized[index] @ centroid), label) for label, centroid in centroids.items())
        nearest_distance, identity = ranked[0]
        margin = ranked[1][0] - ranked[0][0] if len(ranked) > 1 else 1.0
        uncertain = margin < 0.05 or nearest_distance > 0.45
        duration = float(turn["end"]) - float(turn["start"])
        if not uncertain:
            assigned.append({**turn, "identity": identity, "confidence": min(1.0, max(0.0, margin / 0.25))})
        head = str(turn.get("speaker", ""))
        raw_head_votes.setdefault(head, {}).setdefault(identity, 0.0)
        raw_head_votes[head][identity] += duration

    # A short turn cannot produce a reliable embedding. Carry it only when the
    # corresponding raw head is already at least 90% pure against an accepted
    # global identity; otherwise leave that speech unlabeled.
    accepted_keys = {(float(turn["start"]), float(turn["end"]), str(turn.get("speaker", ""))) for turn in embedded_turns}
    for turn in all_turns:
        key = (float(turn["start"]), float(turn["end"]), str(turn.get("speaker", "")))
        if key in accepted_keys:
            continue
        votes = raw_head_votes.get(str(turn.get("speaker", "")), {})
        total = sum(votes.values())
        if not votes or total <= 0:
            continue
        identity, seconds = max(votes.items(), key=lambda item: item[1])
        purity = seconds / total
        if purity >= 0.90:
            assigned.append({**turn, "identity": identity, "confidence": purity})

    if len(centroids) <= 1:
        return {
            "status": "solo",
            "reason": "one_voice_identity",
            "segments": [],
            "identity_count": 1,
            "min_identity_seconds": min_identity_seconds,
            **diagnostics,
        }

    first_start = {
        identity: min(float(turn["start"]) for turn in assigned if turn["identity"] == identity)
        for identity in centroids
        if any(turn["identity"] == identity for turn in assigned)
    }
    stable_order = {identity: index for index, identity in enumerate(sorted(first_start, key=first_start.get))}
    output = []
    for turn in sorted(assigned, key=lambda item: (float(item["start"]), float(item["end"]))):
        identity = turn["identity"]
        if identity not in stable_order:
            continue
        output.append({
            "start": float(turn["start"]),
            "end": float(turn["end"]),
            "duration": float(turn["end"]) - float(turn["start"]),
            "speaker": f"speaker_{stable_order[identity]}",
            "confidence": float(turn.get("confidence", 1.0)),
        })

    return {
        "status": "accepted",
        "segments": output,
        "identity_count": len(stable_order),
        "min_identity_seconds": min_identity_seconds,
        "unlabeled_turns": len(all_turns) - len(output),
        **diagnostics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Recluster Sortformer turns by global voice identity")
    parser.add_argument("audio_file")
    parser.add_argument("turns_file")
    parser.add_argument("output_file")
    parser.add_argument("--max-identities", type=int)
    parser.add_argument("--min-silhouette", type=float, default=MIN_SILHOUETTE)
    args = parser.parse_args()

    with open(args.turns_file, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    turns = payload.get("segments", payload) if isinstance(payload, dict) else payload
    turns = [turn for turn in turns if float(turn.get("end", 0)) > float(turn.get("start", 0))]

    embedded_turns, embeddings = embed_turns(args.audio_file, turns)
    result = recluster(
        turns,
        embedded_turns,
        embeddings,
        max_identities=args.max_identities,
        min_silhouette=args.min_silhouette,
    )
    result.update({
        "model": "nemo/titanet_large",
        "source_turns": len(turns),
        "embedded_turns": len(embedded_turns),
    })
    with open(args.output_file, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(
        f"Voice reclustering {result['status']}: identities={result.get('identity_count', 0)} "
        f"silhouette={result.get('silhouette')} segments={len(result['segments'])}"
    )


if __name__ == "__main__":
    main()
