#!/usr/bin/env python3
"""Sortformer raw heads (before Plume's ghost filter and speaker limit) plus a
replay of the production post-processing, and TitaNet per-turn embeddings.
Runs in the parakeet env with VIRTUAL_ENV set (the managed script derives the
model path from it).
  stage_diar_sortformer.py WAV RAW_OUT PRODUCTION_OUT
  stage_diar_sortformer.py WAV --embed-only TURNS_JSON EMB_OUT"""
import copy, json, os, sys, tempfile, time, wave, logging, warnings
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
import numpy as np, torch
torch.set_num_threads(6)
PARAKEET = os.path.dirname(os.environ["VIRTUAL_ENV"])
sys.path.insert(0, PARAKEET)


def load_wav(path):
    with wave.open(path, "rb") as wf:
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float32) / 32768.0


def embed_turns(wav_path, turns, out, min_dur=1.0, max_crop=20.0):
    from nemo.collections.asr.models import EncDecSpeakerLabelModel
    t0 = time.time()
    m = EncDecSpeakerLabelModel.from_pretrained(model_name="titanet_large", map_location="cpu"); m.eval()
    audio = load_wav(wav_path)
    kept, embs = [], []
    with torch.no_grad():
        for t in turns:
            d = t["end"] - t["start"]
            if d < min_dur:
                continue
            s, e = t["start"], t["end"]
            if d > max_crop:
                mid = (s + e) / 2; s, e = mid - max_crop / 2, mid + max_crop / 2
            seg = audio[int(s * 16000):int(e * 16000)]
            if len(seg) < 16000 * 0.5:
                continue
            emb, _ = m.infer_segment(seg)
            kept.append({"start": t["start"], "end": t["end"], "speaker": t["speaker"]})
            embs.append(emb.reshape(-1).cpu().numpy().astype(float).round(6).tolist())
    json.dump({"model": "nemo titanet_large", "source_turns": len(turns), "embedded_turns": len(kept),
               "min_turn_seconds": min_dur, "max_crop_seconds": max_crop, "turns": kept, "embeddings": embs,
               "seconds": round(time.time() - t0, 1)}, open(out, "w"))
    print(f"titanet embedded {len(kept)}/{len(turns)} turns in {time.time()-t0:.0f}s")


if sys.argv[2] == "--embed-only":
    turns = json.load(open(sys.argv[3]))["segments"]
    embed_turns(sys.argv[1], turns, sys.argv[4]); sys.exit(0)

wav_path, raw_out, prod_out = sys.argv[1:4]
from nemo.collections.asr.models import SortformerEncLabelModel
import sortformer_diarize as plume  # managed copy, byte-identical to the repo

t0 = time.time()
model_path = os.path.join(PARAKEET, "diar_streaming_sortformer_4spk-v2.1.nemo")
dm = SortformerEncLabelModel.restore_from(restore_path=model_path, map_location="cpu", strict=False); dm.eval()
load_s = time.time() - t0
# Production with max_speakers unset uses the 4-speaker NVIDIA baseline thresholds.
yaml_path = plume._create_postprocessing_yaml(4)
t0 = time.time()
pred = dm.diarize(audio=wav_path, batch_size=1, postprocessing_yaml=yaml_path, verbose=False)
diar_s = time.time() - t0
os.unlink(yaml_path)
segs = pred[0] if len(pred) == 1 and isinstance(pred[0], list) else pred
raw = []
for s in segs:
    if isinstance(s, str):
        p = s.split(); raw.append({"start": float(p[0]), "end": float(p[1]), "speaker": p[2]})
    else:
        raw.append({"start": float(s[0]), "end": float(s[1]), "speaker": str(s[2])})
raw.sort(key=lambda x: x["start"])
for r in raw:
    r["duration"] = round(r["end"] - r["start"], 3)
by = {}
for r in raw:
    by[r["speaker"]] = round(by.get(r["speaker"], 0) + r["duration"], 1)
json.dump({"model": "nvidia/diar_streaming_sortformer_4spk-v2.1", "postprocessing": {"onset": 0.64, "offset": 0.74, "pad_onset": 0.06,
           "pad_offset": 0.0, "min_duration_on": 0.1, "min_duration_off": 0.15}, "stage": "raw heads, no ghost filter, no limit",
           "segments": raw, "speakers": sorted(by), "seconds_by_speaker": by, "timings": {"load": round(load_s, 1), "diarize": round(diar_s, 1)}},
          open(raw_out, "w"), indent=1)
prod = plume.filter_ghost_speakers(copy.deepcopy(raw))
spk = {s["speaker"] for s in prod}
if len(spk) > 4:
    prod = plume.limit_speakers(prod, 4)
byp = {}
for r in prod:
    byp[r["speaker"]] = round(byp.get(r["speaker"], 0) + r["end"] - r["start"], 1)
json.dump({"model": "nvidia/diar_streaming_sortformer_4spk-v2.1", "stage": "production replay: filter_ghost_speakers(+limit_speakers if >4)",
           "segments": prod, "speakers": sorted(byp), "seconds_by_speaker": byp}, open(prod_out, "w"), indent=1)
print(f"sortformer raw: {by} ({len(raw)} turns, {diar_s:.0f}s); production replay: {byp}")
