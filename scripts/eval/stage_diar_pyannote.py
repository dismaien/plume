#!/usr/bin/env python3
"""Pyannote speaker-diarization-community-1, loaded offline from the WhisperX
env cache, run unconstrained (optionally also with an exact speaker count), and
per-turn embeddings from the pipeline's own embedding model.
  stage_diar_pyannote.py WAV OUT [--num-speakers N --exact-out PATH]
  stage_diar_pyannote.py WAV --embed-only TURNS_JSON EMB_OUT"""
import json, sys, time, wave, warnings
warnings.filterwarnings("ignore")
import numpy as np, torch
torch.set_num_threads(6)
from pyannote.audio import Pipeline

wav_path = sys.argv[1]


def load_audio(path):
    with wave.open(path, "rb") as wf:
        ch = wf.getnchannels(); sr = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    return {"waveform": torch.from_numpy(pcm.reshape(-1, ch).T.copy()), "sample_rate": sr}


def annotation_of(result):
    for attr in ("speaker_diarization", "diarization"):
        if hasattr(result, attr):
            return getattr(result, attr)
    return result


def to_segments(ann):
    segs = [{"start": round(seg.start, 3), "end": round(seg.end, 3), "speaker": str(label), "duration": round(seg.end - seg.start, 3)}
            for seg, _, label in ann.itertracks(yield_label=True)]
    segs.sort(key=lambda s: s["start"])
    by = {}
    for s in segs:
        by[s["speaker"]] = round(by.get(s["speaker"], 0) + s["duration"], 1)
    return segs, by


t0 = time.time()
pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1")
load_s = time.time() - t0
audio = load_audio(wav_path)

if sys.argv[2] == "--embed-only":
    turns = json.load(open(sys.argv[3]))["segments"]; out = sys.argv[4]
    emb_model = pipe._embedding
    wf, sr = audio["waveform"], audio["sample_rate"]
    kept, embs = [], []
    t0 = time.time()
    with torch.no_grad():
        for t in turns:
            d = t["end"] - t["start"]
            if d < 1.0:
                continue
            s, e = t["start"], t["end"]
            if d > 20.0:
                mid = (s + e) / 2; s, e = mid - 10.0, mid + 10.0
            crop = wf[:, int(s * sr):int(e * sr)].unsqueeze(0)
            emb = emb_model(crop)
            v = np.asarray(emb).reshape(-1)
            if not np.isfinite(v).all():
                continue
            kept.append({"start": t["start"], "end": t["end"], "speaker": t["speaker"]})
            embs.append(v.astype(float).round(6).tolist())
    json.dump({"model": "pyannote community-1 embedding (" + type(emb_model).__name__ + ")", "source_turns": len(turns),
               "embedded_turns": len(kept), "min_turn_seconds": 1.0, "max_crop_seconds": 20.0, "turns": kept,
               "embeddings": embs, "seconds": round(time.time() - t0, 1)}, open(out, "w"))
    print(f"pyannote embedded {len(kept)}/{len(turns)} turns in {time.time()-t0:.0f}s")
    sys.exit(0)

out = sys.argv[2]
t0 = time.time()
res = pipe(audio)
diar_s = time.time() - t0
segs, by = to_segments(annotation_of(res))
json.dump({"model": "pyannote/speaker-diarization-community-1", "params": pipe.parameters(instantiated=True),
           "constraints": None, "segments": segs, "speakers": sorted(by), "seconds_by_speaker": by,
           "timings": {"load": round(load_s, 1), "diarize": round(diar_s, 1)}}, open(out, "w"), indent=1)
print(f"pyannote unconstrained: {by} ({len(segs)} turns, {diar_s:.0f}s)")
if "--num-speakers" in sys.argv:
    n = int(sys.argv[sys.argv.index("--num-speakers") + 1]); eo = sys.argv[sys.argv.index("--exact-out") + 1]
    t0 = time.time(); res2 = pipe(audio, num_speakers=n); d2 = time.time() - t0
    segs2, by2 = to_segments(annotation_of(res2))
    json.dump({"model": "pyannote/speaker-diarization-community-1", "constraints": {"num_speakers": n}, "segments": segs2,
               "speakers": sorted(by2), "seconds_by_speaker": by2, "timings": {"diarize": round(d2, 1)}}, open(eo, "w"), indent=1)
    print(f"pyannote exact-{n}: {by2} ({len(segs2)} turns, {d2:.0f}s)")
