#!/usr/bin/env python3
"""Reproduce the bounded decoder-timing attribution experiment."""

import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "internal/transcription/adapters/py/nvidia"))

from alignment_attribution import build_evaluation_proposals, flatten_decoder_words
from score_reference import digest, is_known_speaker, score


def normalized(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(c for c in value if not unicodedata.category(c).startswith("P")).strip()


def per_person_wer(reference_words, candidate_words, assignment):
    import meeteval.wer

    mapping = {str(reference): hypothesis for reference, hypothesis in assignment}
    result = {}
    for person in sorted({str(word["speaker"]) for word in reference_words}):
        reference = " ".join(filter(None, (normalized(word["word"]) for word in reference_words
                                            if str(word["speaker"]) == person)))
        hypothesis_label = mapping.get(person)
        hypothesis = " ".join(filter(None, (normalized(word["word"]) for word in candidate_words
                                             if hypothesis_label is not None
                                             and is_known_speaker(word.get("speaker"))
                                             and str(word["speaker"]) == str(hypothesis_label))))
        result[person] = dataclasses.asdict(meeteval.wer.siso_word_error_rate(reference, hypothesis))
    return result


def write_new(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        json.dump(payload, handle, indent=2, allow_nan=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostics-root", required=True, type=Path)
    parser.add_argument("--decoder", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--benchmark-out", required=True, type=Path)
    args = parser.parse_args()
    root, out = args.diagnostics_root, args.out
    baseline_path = root / "actual-asr-refinement-v2/baseline.json"
    native_path = root / "threshold-tuning/two-speakers/threshold-40.json"
    reference_path = root.parent / "durability-20261107/oracle-words-verified/two-speakers/reference.json"
    transcript_path = root / "actual-asr-plume-input.json"
    diarization_path = root.parent / "durability-20261107/ami-benchmark/two-speakers/community1-auto.json"
    assembler = root / "bin/transcription-eval-refinement"
    baseline = json.loads(baseline_path.read_text())
    native = json.loads(native_path.read_text())
    decoder = json.loads(args.decoder.read_text())
    proposals = build_evaluation_proposals(
        baseline["diarization_result"], native, baseline["word_segments"], flatten_decoder_words(decoder)
    )
    proposals_path, candidate_path = out / "proposals.json", out / "candidate.json"
    write_new(proposals_path, proposals)
    process = subprocess.run([
        str(assembler), "-transcript", str(transcript_path), "-diarization", str(diarization_path),
        "-word-proposals", str(proposals_path), "-out", str(candidate_path)
    ], capture_output=True, text=True, timeout=60)
    if process.returncode:
        raise RuntimeError(process.stderr)
    baseline_score = score(reference_path, baseline_path)
    candidate_score = score(reference_path, candidate_path)
    manifest = json.loads(reference_path.read_text())
    reference_words = json.loads((reference_path.parent / manifest["words"]).read_text())
    candidate = json.loads(candidate_path.read_text())
    immutable_fields = ("word", "start", "end")
    if candidate.get("text") != baseline.get("text") or len(candidate["word_segments"]) != len(baseline["word_segments"]):
        raise AssertionError("Candidate changed complete text or aligned word count")
    if any(any(before.get(field) != after.get(field) for field in immutable_fields)
           for before, after in zip(baseline["word_segments"], candidate["word_segments"])):
        raise AssertionError("Candidate changed accepted word text or timestamps")
    assignment = candidate_score["words"]["cpwer"]["assignment"]

    shared = json.loads((root / "shared-core-results/results.json").read_text())
    parity = []
    for directory in sorted((root / "shared-core-results").iterdir()):
        if not directory.is_dir():
            continue
        current = json.loads((directory / "proposals.json").read_text())
        source = json.loads((directory / "baseline.json").read_text())
        native_case = json.loads((directory / "native40.json").read_text())
        replay = build_evaluation_proposals(source["diarization_result"], native_case,
                                             source["word_segments"], [])
        parity.append({"case": directory.name,
                       "proposals_identical_without_decoder_evidence": replay["proposals"] == current["proposals"],
                       "mapping_identical_without_decoder_evidence": replay["mapping"] == current["mapping"]})
    if not all(item["proposals_identical_without_decoder_evidence"]
               and item["mapping_identical_without_decoder_evidence"] for item in parity):
        raise AssertionError("No-sidecar parity failed")

    report = {
        "status": "scored_evaluation_only_rejected_for_production",
        "experiment": "reference-free decoder timing plus trailing native activity island",
        "decision": "timing smear confirmed; anonymous identity hypothesis not production-safe",
        "inputs": {
            "decoder_sha256": digest(args.decoder),
            "baseline_sha256": digest(baseline_path),
            "native_sha256": digest(native_path),
            "reference_sha256": digest(reference_path),
            "assembler_sha256": digest(assembler),
        },
        "manual_word_cases": {
            "count": len(parity),
            "no_decoder_evidence_parity": parity,
            "existing_scores_unchanged": [{
                "excerpt": item["excerpt"], "split": item["split"], "policy": item["policy"],
                "cpwer": item["cpwer"], "attribution": item["attribution"]
            } for item in shared["results"]],
            "reserved_policy": "TS3003a labels were read only for final scoring; no threshold or rule changed after scoring",
        },
        "actual_asr_pilot": {
            "alignment_diagnostics": proposals["alignment_diagnostics"],
            "proposal_count": len(proposals["proposals"]),
            "text_and_word_timestamps_preserved": True,
            "baseline": {
                "cpwer": baseline_score["words"]["cpwer"],
                "complete_text_wer": baseline_score["words"]["full_text_wer"],
                "per_person_wer": per_person_wer(reference_words, baseline["word_segments"],
                                                  baseline_score["words"]["cpwer"]["assignment"]),
                "diarization": baseline_score["diarization"],
            },
            "candidate": {
                "cpwer": candidate_score["words"]["cpwer"],
                "complete_text_wer": candidate_score["words"]["full_text_wer"],
                "per_person_wer": per_person_wer(reference_words, candidate["word_segments"], assignment),
                "diarization": candidate_score["diarization"],
            },
        },
        "safety": [
            "No production or live database write",
            "Transcript text and accepted timestamps unchanged",
            "No accepted speaker name changed",
            "Community-1 remains primary",
            "The added label is an evaluation-only anonymous hypothesis from an unmapped native channel",
            "One public 60-second meeting excerpt is insufficient to promote this rule",
        ],
        "artifacts": {
            "proposal_sha256": digest(proposals_path),
            "candidate_sha256": digest(candidate_path),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    }
    write_new(args.benchmark_out, report)
    print(json.dumps({
        "baseline_cpwer": baseline_score["words"]["cpwer"]["error_rate"],
        "candidate_cpwer": candidate_score["words"]["cpwer"]["error_rate"],
        "full_text_wer": candidate_score["words"]["full_text_wer"]["error_rate"],
        "proposals": len(proposals["proposals"]),
        "manual_cases": len(parity),
    }, indent=2))


if __name__ == "__main__":
    main()
