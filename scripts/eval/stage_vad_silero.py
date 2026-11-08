#!/usr/bin/env python3
"""Reproduce Plume's Silero pre-strip exactly (pipeline/vad_silero.go) and
record the discarded intervals. Runs in the silero-vad env."""
import json, sys, time, wave
import numpy as np, torch

src, out_json, out_wav = sys.argv[1:4]
SR = 16000
t0 = time.time()
model, utils = torch.hub.load("snakers4/silero-vad", "silero_vad", trust_repo=True, skip_validation=True)
get_speech_timestamps = utils[0]
with wave.open(src, "rb") as wf:
    assert wf.getframerate() == SR and wf.getsampwidth() == 2
    frames = wf.readframes(wf.getnframes()); ch = wf.getnchannels()
audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
if ch > 1:
    audio = audio.reshape(-1, ch).mean(axis=1)
wav = torch.from_numpy(np.ascontiguousarray(audio))
dur = len(wav) / SR
settings = dict(threshold=0.35, min_speech_duration_ms=250, min_silence_duration_ms=700, speech_pad_ms=300)
ts = get_speech_timestamps(wav, model, sampling_rate=SR, return_seconds=False, **settings)
regions = [{"start": round(t["start"] / SR, 6), "end": round(t["end"] / SR, 6)} for t in ts]
chunks = [wav[t["start"]:t["end"]] for t in ts]
stripped = torch.cat(chunks) if chunks else wav
if not chunks:
    regions = [{"start": 0.0, "end": float(dur)}]
pcm = np.clip(stripped.numpy() * 32767.0, -32768, 32767).astype("<i2")
with wave.open(out_wav, "wb") as wf:
    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR); wf.writeframes(pcm.tobytes())
discarded, cursor = [], 0.0
for r in regions:
    if r["start"] > cursor + 1e-6:
        discarded.append({"start": round(cursor, 3), "end": round(r["start"], 3)})
    cursor = r["end"]
if dur > cursor + 1e-6:
    discarded.append({"start": round(cursor, 3), "end": round(dur, 3)})
kept = sum(r["end"] - r["start"] for r in regions)
json.dump({"source": src, "stripped_wav": out_wav, "original_duration": round(dur, 6), "regions": regions,
           "discarded": discarded, "kept_seconds": round(kept, 3), "kept_ratio": round(kept / dur, 4) if dur else None,
           "longest_discarded_seconds": round(max([d["end"] - d["start"] for d in discarded] or [0]), 3),
           "settings": {"model": "snakers4/silero-vad torch.hub", **settings, "source_file": "pipeline/vad_silero.go"},
           "seconds": round(time.time() - t0, 1)}, open(out_json, "w"), indent=1)
print(f"VAD kept {kept:.1f}s of {dur:.1f}s ({kept/dur*100:.1f}%), {len(regions)} regions, {len(discarded)} discarded")
