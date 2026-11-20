#!/usr/bin/env python3
"""ASR candidates in the WhisperX env. Modes:
  silero : WhisperX batched pipeline on Plume's Silero-stripped audio with the
           importer profile (large-v3 int8, Silero VAD 0.5/0.363, lp -0.5,
           nst 0.6, temperature fallback), aligned, then remapped to source time.
  full   : faster-whisper timestamp decoding over the full timeline (the
           whisper_full_audio.py behaviour, default-profile thresholds), then
           WhisperX alignment; alignment failure keeps the unaligned words.
  hybrid : full-timeline decode, then evidence-based removal of segments that
           both Silero and Whisper's no_speech evidence call silence.
Writes words with source-time stamps, per-segment decoder diagnostics,
language evidence, alignment status, and timings."""
import argparse, json, re, time, wave
import numpy as np, torch
import whisperx
from faster_whisper import WhisperModel

ap = argparse.ArgumentParser()
ap.add_argument("--mode", required=True, choices=["silero", "full", "hybrid"])
ap.add_argument("--audio", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--manifest"); ap.add_argument("--stripped"); ap.add_argument("--reuse-full")
ap.add_argument("--full-nst", type=float, default=0.5)
ap.add_argument("--language", default=None)
a = ap.parse_args()
torch.set_num_threads(6)
T = {}


def tick(k, t0):
    T[k] = round(time.time() - t0, 1)


def load_wav(path):
    with wave.open(path, "rb") as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    return pcm


def remap(t, regions):
    if not regions:
        return t
    if t <= 0:
        return regions[0]["start"]
    acc = 0.0
    for r in regions:
        d = r["end"] - r["start"]
        if t < acc + d:
            return r["start"] + (t - acc)
        acc += d
    return regions[-1]["end"]


def align_or_keep(segments, audio, language, fallback_words):
    """WhisperX alignment with fail-open fallback to decoder word times."""
    t0 = time.time()
    try:
        am, meta = whisperx.load_align_model(language, "cpu")
        res = whisperx.align(segments, am, meta, audio, "cpu", interpolate_method="nearest", return_char_alignments=False)
        tick("align", t0)
        words = [{"word": w.get("word"), "start": w.get("start"), "end": w.get("end"), "score": w.get("score")}
                 for w in res["word_segments"] if w.get("start") is not None]
        segs = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in res["segments"]]
        return segs, words, {"aligned": True, "model": meta.get("type"), "language": language}
    except Exception as e:
        tick("align", t0)
        return segments, fallback_words, {"aligned": False, "language": language, "error": f"{type(e).__name__}: {e}"[:300]}


FILLER = re.compile(r"^\W*(thank you|thanks for watching|please subscribe|see you next time|vielen dank|danke|"
                    r"terima kasih telah menonton|thank you for watching|bye|the end)\W*$", re.I)
out = {"mode": a.mode, "audio": a.audio, "settings": {}, "timings": T}

if a.mode == "silero":
    man = json.load(open(a.manifest)); regions = man["regions"]
    t0 = time.time()
    asr_options = {"beam_size": 5, "best_of": 5, "patience": 1, "length_penalty": 1,
                   "temperatures": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0], "compression_ratio_threshold": 2.4,
                   "log_prob_threshold": -0.5, "no_speech_threshold": 0.6, "condition_on_previous_text": False,
                   "initial_prompt": None, "hotwords": None, "suppress_tokens": [-1], "suppress_numerals": False}
    model = whisperx.load_model("large-v3", "cpu", compute_type="int8", asr_options=asr_options, language=a.language,
                                vad_method="silero", vad_options={"chunk_size": 30, "vad_onset": 0.5, "vad_offset": 0.363},
                                local_files_only=True, threads=6)
    tick("load", t0); t0 = time.time()
    audio = whisperx.load_audio(a.stripped)
    res = model.transcribe(audio, batch_size=8, chunk_size=30, print_progress=False)
    tick("asr", t0)
    lang = res["language"]
    segs_s = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in res["segments"]]
    segs, words, align = align_or_keep(res["segments"], audio, lang, [])
    # production remaps both segments and words through the VAD manifest
    for s in segs:
        s["start_stripped"], s["end_stripped"] = s["start"], s["end"]
        s["start"], s["end"] = remap(s["start"], regions), remap(s["end"], regions)
    for w in words:
        w["start"], w["end"] = remap(w["start"], regions), remap(w["end"], regions)
    out.update(language=lang, language_evidence="whisperx first-30s detection on stripped audio",
               alignment=align, segments=segs, words=words, stripped_segments=segs_s,
               settings={"profile": "importer qualityParameters()", "model": "large-v3", "compute_type": "int8",
                         "vad_prestrip": "plume silero 0.35/250/700/300", "whisperx_vad": "silero onset .5 offset .363",
                         "chunk_size": 30, "batch_size": 8, "log_prob_threshold": -0.5, "no_speech_threshold": 0.6,
                         "temperatures": asr_options["temperatures"], "condition_on_previous_text": False})
else:
    lang_evidence = "faster-whisper whole-file detection (first 30 s of raw audio)"
    forced = a.language
    t0 = time.time()
    model = WhisperModel("large-v3", device="cpu", compute_type="int8", cpu_threads=6, local_files_only=True)
    tick("load", t0)
    if a.mode == "hybrid" and forced is None:
        # v2: detect language on speech, not on the possibly silent file head.
        stripped = load_wav(a.stripped)
        t0 = time.time()
        try:
            lang_s, prob_s, all_probs = model.detect_language(stripped[: 16000 * 30])
        except Exception:
            lang_s, prob_s, all_probs = model.detect_language(audio=stripped[: 16000 * 30])
        tick("lang_detect", t0)
        forced, lang_evidence = lang_s, f"faster-whisper detection on first 30 s of Silero-stripped speech (p={prob_s:.2f})"
    t0 = time.time()
    audio = load_wav(a.audio)
    segments, info = model.transcribe(audio, language=forced, task="transcribe", beam_size=5, best_of=5, patience=1,
                                      length_penalty=1, temperature=[0.0], compression_ratio_threshold=2.4,
                                      log_prob_threshold=-0.5, no_speech_threshold=a.full_nst,
                                      condition_on_previous_text=False, word_timestamps=True, vad_filter=False)
    decoded = []
    for s in segments:
        decoded.append({"id": s.id, "start": s.start, "end": s.end, "text": s.text, "avg_logprob": s.avg_logprob,
                        "no_speech_prob": s.no_speech_prob, "compression_ratio": s.compression_ratio,
                        "temperature": s.temperature,
                        "words": [{"word": w.word, "start": w.start, "end": w.end, "probability": w.probability} for w in (s.words or [])]})
    tick("asr", t0)
    lang, info_lp = info.language, float(info.language_probability)
    del model
    kept, dropped, flagged = decoded, [], []
    if a.mode == "hybrid":
        man = json.load(open(a.manifest)); regions = man["regions"]

        def speech_cov(s):
            d = max(s["end"] - s["start"], 1e-6)
            ov = sum(max(0.0, min(s["end"], r["end"]) - max(s["start"], r["start"])) for r in regions)
            return ov / d
        kept, dropped, flagged = [], [], []
        for s in decoded:
            cov = speech_cov(s)
            reasons, flags = [], []
            txt = s["text"].strip() or "x"
            if cov < 0.10 and FILLER.match(txt):
                reasons.append("no_silero_speech_and_closing_filler_text")
            if cov < 0.10 and s["no_speech_prob"] >= 0.60 and s["avg_logprob"] < -0.60:
                reasons.append("no_silero_speech_and_low_confidence")
            if s["compression_ratio"] > 2.4 and cov < 0.5:
                reasons.append("high_compression_low_speech")
            if not reasons and cov < 0.10:
                flags.append("speech_outside_silero_regions_review")
            s = dict(s, silero_speech_coverage=round(cov, 3))
            if reasons:
                dropped.append(dict(s, drop_reasons=reasons))
            else:
                kept.append(s)
                if flags:
                    flagged.append(dict(s, flags=flags))
    seg_in = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in kept]
    fallback = [{"word": w["word"], "start": w["start"], "end": w["end"], "score": w["probability"]} for s in kept for w in s["words"]]
    segs, words, align = align_or_keep(seg_in, audio, lang, fallback)
    out.update(language=lang, language_probability=info_lp, language_evidence=lang_evidence,
               alignment=align, segments=segs, words=words, decoder_segments=decoded,
               dropped_segments=dropped, flagged_segments=flagged if a.mode == "hybrid" else [],
               settings={"profile": "default profile 'Archive Recovery (WhisperX)' decoder" if a.mode == "full" else "hybrid v1",
                         "model": "large-v3", "compute_type": "int8", "vad_filter": False, "temperatures": [0.0],
                         "log_prob_threshold": -0.5, "no_speech_threshold": a.full_nst, "condition_on_previous_text": False,
                         "hybrid_rule": "v2: language from Silero-stripped speech; drop iff Silero(0.35) coverage <10% AND (closing-filler text OR (no_speech>=0.6 AND avg_logprob<-0.6)); or compression>2.4 with coverage<50%; other speech outside Silero regions is kept and flagged" if a.mode == "hybrid" else None})
out["word_count"] = len(out["words"])
out["last_word_end"] = max([w["end"] for w in out["words"]] or [0])
json.dump(out, open(a.out, "w"), indent=1, ensure_ascii=False)
print(f"{a.mode}: lang={out['language']} words={out['word_count']} aligned={out['alignment']['aligned']} timings={T}"
      + (f" dropped={len(out.get('dropped_segments', []))} flagged={len(out.get('flagged_segments', []))}" if a.mode == "hybrid" else ""))
