"""Compare an independent original-audio reading with a fresh timed transcript."""

import argparse
import bisect
import difflib
import json
import math
from pathlib import Path


def normalize(word):
    return "".join(c for c in word.casefold() if c.isalnum())


def source_words(doc, duration):
    """Keep segment wording, including words omitted by the alignment model."""
    aligned = doc.get("word_segments") or []
    sections = doc.get("segments") or []
    if not sections and aligned:
        return [
            (w["word"].strip(), w["start"], w["end"])
            for w in aligned
            if w["word"].strip()
        ]
    if not sections and doc.get("text", "").strip():
        if duration > 30:
            raise ValueError("No timing for comparison")
        sections = [{"text": doc["text"], "start": 0, "end": duration}]
    words = []
    j = 0
    for section in sections:
        start, end = section["start"], section["end"]
        tokens = section["text"].split()
        if not tokens:
            continue
        group = []
        while j < len(aligned):
            w = aligned[j]
            midpoint = (w["start"] + w["end"]) / 2
            if midpoint > end:
                break
            if midpoint >= start:
                group.append(w)
            j += 1
        # Interpolated boundaries only locate context in this comparison.
        # The stored transcript and its timing are never reconstructed here.
        times = [
            (
                start + (end - start) * i / len(tokens),
                start + (end - start) * (i + 1) / len(tokens),
            )
            for i in range(len(tokens))
        ]
        matches = difflib.SequenceMatcher(
            None,
            [normalize(w["word"]) for w in group],
            [normalize(t) for t in tokens],
            autojunk=False,
        )
        for a, b, n in matches.get_matching_blocks():
            for k in range(n):
                times[b + k] = (group[a + k]["start"], group[a + k]["end"])
        words.extend((t, *stamp) for t, stamp in zip(tokens, times))
    return words


def windows(words, duration):
    # Prefer a real speech gap near the cut. Consume overlapping words once
    # in transcript order, even if two speakers share a timestamp.
    cuts = sorted(
        ((left[2] + right[1]) / 2, right[1] - left[2])
        for left, right in zip(words, words[1:])
        if left[2] <= right[1]
    )
    centers = [t for t, _ in cuts]
    start, i = 0.0, 0
    while start < duration:
        end = duration
        if duration - start > 30:
            choices = cuts[
                bisect.bisect_left(centers, start + 18) : bisect.bisect_right(
                    centers, start + 28
                )
            ]
            end = (
                max(choices, key=lambda c: (min(c[1], 1), c[0]))[0]
                if choices
                else start + 24
            )
        j = i
        while j < len(words) and (
            end == duration or (words[j][1] + words[j][2]) / 2 < end
        ):
            j += 1
        yield start, end, words[i:j]
        start, i = end, j


def differences(words, heard, start, end):
    alt = heard.split()
    first = [w for w in words if normalize(w[0])]
    alt = [w for w in alt if normalize(w)]
    matcher = difflib.SequenceMatcher(
        None,
        [normalize(w[0]) for w in first],
        [normalize(w) for w in alt],
        autojunk=False,
    )
    for group in matcher.get_grouped_opcodes(3):
        a, b = group[0][1], group[-1][2]
        c, d = group[0][3], group[-1][4]
        edit = next(op for op in group if op[0] != "equal")
        target = start
        if first:
            anchor = first[min(edit[1], len(first) - 1)]
            target = (anchor[1] + anchor[2]) / 2
        yield {
            "start": start,
            "end": end,
            "target": max(start, min(end, target)),
            "original": " ".join(w[0] for w in first[a:b]),
            "alternative": " ".join(alt[c:d]),
        }


def main():
    import numpy as np
    import torch
    from transformers import AutoProcessor, AutoModelForMultimodalLM

    parser = argparse.ArgumentParser()
    for arg in ("model", "pcm", "source", "out"):
        parser.add_argument("--" + arg, type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(3)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    processor = AutoProcessor.from_pretrained(args.model, local_files_only=True)
    model = (
        AutoModelForMultimodalLM.from_pretrained(
            args.model,
            dtype=torch.float32,
            attn_implementation="sdpa",
            local_files_only=True,
        )
        .to(device)
        .eval()
    )
    pcm = np.memmap(args.pcm, dtype="<i2", mode="r")
    duration = len(pcm) / 16000
    words = source_words(json.loads(args.source.read_text()), duration)
    if any(not math.isfinite(s + e) or e < s for _, s, e in words):
        raise ValueError("Invalid transcript timing")
    report = {"status": "complete", "checked": 0, "incomplete": 0, "differences": []}
    for start, end, batch in windows(words, duration):
        audio = pcm[round(start * 16000) : round(end * 16000)].astype("float32") / 32768
        inputs = processor.apply_transcription_request(audio=audio).to(
            device, torch.float32
        )
        with torch.inference_mode():
            ids = model.generate(**inputs, do_sample=False, max_new_tokens=350)[
                :, inputs["input_ids"].shape[1] :
            ]
        if ids.shape[1] >= 350:
            report["incomplete"] += 1
            continue
        heard = processor.decode(ids, return_format="parsed")[0]["transcription"]
        report["differences"].extend(differences(batch, heard, start, end))
        report["checked"] += 1
    args.out.write_text(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
