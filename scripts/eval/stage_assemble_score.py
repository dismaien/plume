#!/usr/bin/env python3
"""Assemble words + turns with the production rule (per-word max overlap) for
every ASR x diarizer x recluster combination present in the job dir, and score
text, coverage, speakers, and anchors. Stdlib only. Writes assembled-*.json,
assembled-*.md and score.json; prints a compact report."""
import argparse, itertools, json, re
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--jobdir", required=True, type=Path); ap.add_argument("--anchors", default="[]")
ap.add_argument("--mode", default="score"); ap.add_argument("--live")
a = ap.parse_args()
J = a.jobdir; anchors = json.loads(a.anchors)
FILLERS = {"en": ["thank you", "thanks for watching", "please subscribe", "see you next time", "thank you for watching"],
           "de": ["vielen dank", "danke", "bis zum nächsten mal"], "id": ["terima kasih telah menonton", "terima kasih"]}


def words_of(text):
    return re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)?", text.lower())


def filler_counts(text):
    ws = words_of(text); out = {}
    for lang, phrases in FILLERS.items():
        for ph in phrases:
            p = ph.split(); n = 0; i = 0
            while i <= len(ws) - len(p):
                if ws[i:i + len(p)] == p:
                    n += 1; i += len(p)
                else:
                    i += 1
            if n:
                out[ph] = n
    return out, len(ws)


def repeated_runs(ws, min_run=8):
    runs = 0; cur = 1
    for x, y in zip(ws, ws[1:]):
        if x == y:
            cur += 1
        else:
            runs += cur >= min_run; cur = 1
    return runs + (cur >= min_run)


def speech_seconds(items):
    iv = sorted((i["start"], i["end"]) for i in items if i.get("start") is not None)
    tot = 0.0; cs = ce = None
    for s, e in iv:
        if cs is None or s > ce:
            if cs is not None:
                tot += ce - cs
            cs, ce = s, e
        else:
            ce = max(ce, e)
    return tot + ((ce - cs) if cs is not None else 0.0)


def assign(words, turns):
    """Production findBestSpeakerForSegment: speaker of the turn with maximum overlap."""
    turns = sorted(turns, key=lambda t: t["start"]); out = []; j = 0
    for w in words:
        best, bo = None, 0.0
        while j < len(turns) and turns[j]["end"] < w["start"] - 30:
            j += 1
        for t in turns[j:]:
            if t["start"] > w["end"]:
                break
            ov = max(0.0, min(w["end"], t["end"]) - max(w["start"], t["start"]))
            if ov > bo:
                bo, best = ov, t["speaker"]
        out.append(dict(w, speaker=best))
    return out


def speaker_stats(words):
    sec, cnt = {}, {}
    for w in words:
        k = w.get("speaker") or "<none>"
        sec[k] = sec.get(k, 0) + max(0.0, w["end"] - w["start"]); cnt[k] = cnt.get(k, 0) + 1
    changes = sum(1 for x, y in zip(words, words[1:]) if (x.get("speaker") or "") != (y.get("speaker") or ""))
    labels = [k for k in sec if k != "<none>"]
    total = sum(sec[k] for k in labels) or 1
    return {"labels": len(labels), "seconds_by_label": {k: round(v, 1) for k, v in sorted(sec.items(), key=lambda x: -x[1])},
            "words_by_label": dict(sorted(cnt.items(), key=lambda x: -x[1])), "unlabeled_words": cnt.get("<none>", 0),
            "unlabeled_seconds": round(sec.get("<none>", 0), 1), "speaker_changes": changes,
            "substantive_labels": sum(1 for k in labels if sec[k] >= max(30, 0.05 * total))}


def to_md(words, names=None):
    names = names or {}; out = []; cur = object(); buf = []
    for w in words:
        s = w.get("speaker")
        if s != cur:
            if buf:
                out.append((names.get(cur, cur) if cur else None, " ".join(buf)))
            cur, buf = s, []
        buf.append((w["word"] or "").strip())
    if buf:
        out.append((names.get(cur, cur) if cur else None, " ".join(buf)))
    return "\n\n".join((f"**{spk}**\n\n{txt}" if spk else f"**(unlabeled)**\n\n{txt}") for spk, txt in out)


def anchor_report(words, turns_name):
    rep = []
    for st, en, label in anchors:
        ws = [w for w in words if w["end"] >= st and w["start"] <= en]
        runs = []
        for w in ws:
            s = w.get("speaker") or "<none>"
            if runs and runs[-1]["speaker"] == s:
                runs[-1]["end"] = w["end"]; runs[-1]["text"] += " " + (w["word"] or "").strip()
            else:
                runs.append({"speaker": s, "start": round(w["start"], 1), "end": round(w["end"], 1), "text": (w["word"] or "").strip()})
        rep.append({"anchor": [st, en, label], "words": len(ws), "runs": runs})
    return rep


def agreement(wa, wb):
    """Per-word label agreement between two assemblies after best label permutation (<=5 labels)."""
    la = sorted({w.get("speaker") for w in wa if w.get("speaker")}); lb = sorted({w.get("speaker") for w in wb if w.get("speaker")})
    if not la or not lb or len(la) > 5 or len(lb) > 5:
        return None
    pairs = [(x.get("speaker"), y.get("speaker")) for x, y in zip(wa, wb) if x.get("speaker") and y.get("speaker")]
    if not pairs:
        return None
    small, big = (la, lb) if len(la) <= len(lb) else (lb, la)
    best = 0
    for perm in itertools.permutations(big, len(small)):
        m = dict(zip(small, perm))
        if len(la) <= len(lb):
            ok = sum(1 for x, y in pairs if m.get(x) == y)
        else:
            ok = sum(1 for x, y in pairs if m.get(y) == x)
        best = max(best, ok)
    return round(best / len(pairs), 3)


# ---- collect inputs
asr = {}
for p in sorted(J.glob("asr-*.json")):
    asr[p.stem.replace("asr-", "")] = json.load(open(p))
if a.live and Path(a.live).exists():
    live = json.load(open(a.live))
    asr["live"] = {"words": [{"word": w["word"], "start": w["start"], "end": w["end"], "score": w.get("score"), "live_speaker": w.get("speaker")}
                             for w in live.get("word_segments", [])], "language": live.get("language"),
                   "segments": live.get("segments", []), "alignment": {"aligned": True, "source": "live transcript.json"}, "mode": "live"}
# Synthesized candidate "union": Silero-path words as the base (production text),
# plus hybrid-filtered full-timeline words only where the base has no word
# within 2 s. Uses outputs already on disk; no decoding.
if "silero" in asr and "hybrid" in asr:
    base = sorted(asr["silero"]["words"], key=lambda w: w["start"])
    starts = [w["start"] for w in base]
    import bisect
    added = []
    for w in asr["hybrid"]["words"]:
        i = bisect.bisect_left(starts, w["start"] - 2.0)
        near = any(abs(base[k]["start"] - w["start"]) <= 2.0 or (base[k]["start"] <= w["end"] and base[k]["end"] >= w["start"]) for k in range(i, min(len(base), i + 12)))
        if not near:
            added.append(dict(w, union_added=True))
    merged = sorted(base + added, key=lambda w: w["start"])
    asr["union"] = {"words": merged, "language": asr["silero"].get("language"), "segments": [],
                    "alignment": {"aligned": True, "source": "silero words + hybrid words outside Silero coverage"},
                    "mode": "union", "flagged_segments": asr["hybrid"].get("flagged_segments"),
                    "union_added_words": len(added),
                    "union_added_spans": [{"start": round(w["start"], 1), "word": w["word"]} for w in added][:400]}
turnsets = {}
for p in sorted(J.glob("diar-*.json")):
    turnsets[p.stem.replace("diar-", "")] = json.load(open(p))["segments"]
for p in sorted(J.glob("recluster-*.json")):
    r = json.load(open(p))
    if r.get("turns"):
        turnsets[p.stem.replace("recluster-", "")] = [{"start": t["start"], "end": t["end"], "speaker": f"c{t['cluster']}" + ("?" if t["uncertain"] else "")} for t in r["turns"]]
src = json.load(open(J / "source.json")) if (J / "source.json").exists() else {}
vad = json.load(open(J / "vad-plume-silero.json")) if (J / "vad-plume-silero.json").exists() else {}
dur = src.get("ffprobe_seconds")

score = {"source_seconds": dur, "vad": {k: vad.get(k) for k in ("kept_seconds", "kept_ratio", "longest_discarded_seconds")}, "asr": {}, "assemblies": {}, "lost_spans": {}}
for name, r in asr.items():
    text = " ".join((w["word"] or "") for w in r["words"])
    fc, n = filler_counts(text)
    ws = words_of(text)
    score["asr"][name] = {"language": r.get("language"), "words": len(r["words"]), "speech_seconds": round(speech_seconds(r["words"]), 1),
                          "last_word_end": round(max([w["end"] for w in r["words"]] or [0]), 1), "fillers": fc,
                          "filler_word_share": round(sum(c * len(p.split()) for p, c in fc.items()) / max(len(ws), 1), 4),
                          "repeated_runs_ge8": repeated_runs(ws), "aligned": r.get("alignment", {}).get("aligned"),
                          "align_error": r.get("alignment", {}).get("error"), "dropped_segments": len(r.get("dropped_segments", [])),
                          "timings": r.get("timings"), "low_score_words": sum(1 for w in r["words"] if (w.get("score") or 1) < 0.3)}
    if name == "union":
        spans = []
        for w in r["words"]:
            if not w.get("union_added"):
                continue
            if spans and w["start"] - spans[-1]["end"] < 2.0:
                spans[-1]["end"] = w["end"]; spans[-1]["text"] += " " + (w["word"] or "").strip(); spans[-1]["n"] += 1
            else:
                spans.append({"start": round(w["start"], 1), "end": w["end"], "text": (w["word"] or "").strip(), "n": 1})
        score["asr"][name]["added_spans"] = [dict(x, end=round(x["end"], 1)) for x in spans]
        score["asr"][name]["added_words"] = r.get("union_added_words")
    fl = r.get("flagged_segments") or []
    if fl:
        filler_like = [x for x in fl if x.get("no_speech_prob", 0) >= 0.6 and len(x["text"].split()) <= 3]
        quiet = [x for x in fl if x not in filler_like]
        score["asr"][name]["flagged"] = {"total": len(fl), "likely_silence_filler": len(filler_like),
                                         "plausible_quiet_speech": [{"start": round(x["start"], 1), "end": round(x["end"], 1),
                                                                     "no_speech_prob": round(x["no_speech_prob"], 2), "text": x["text"].strip()[:120]} for x in quiet]}
    if r.get("dropped_segments"):
        score["asr"][name]["dropped_examples"] = [{"start": round(x["start"], 1), "reasons": x["drop_reasons"], "text": x["text"].strip()[:80]} for x in r["dropped_segments"][:40]]
# lost spans: words in A with no B word within 0.5 s overlap, grouped
names = list(asr)
for A, B in itertools.permutations(names, 2):
    wb = sorted(asr[B]["words"], key=lambda w: w["start"]); lost = []
    for w in asr[A]["words"]:
        if not any(min(w["end"], x["end"]) - max(w["start"], x["start"]) > -0.5 for x in wb if abs(x["start"] - w["start"]) < 5):
            lost.append(w)
    spans = []
    for w in lost:
        if spans and w["start"] - spans[-1]["end"] < 2.0:
            spans[-1]["end"] = w["end"]; spans[-1]["text"] += " " + (w["word"] or "").strip(); spans[-1]["n"] += 1
        else:
            spans.append({"start": round(w["start"], 1), "end": w["end"], "text": (w["word"] or "").strip(), "n": 1})
    spans = [dict(s, end=round(s["end"], 1)) for s in spans if s["n"] >= 3]
    score["lost_spans"][f"in_{A}_not_in_{B}"] = {"spans": len(spans), "words": sum(s["n"] for s in spans), "examples": spans[:25]}

mapping = {m["original_speaker"]: m["custom_name"] for m in json.load(open(J / "manifest.json")).get("speaker_mappings", [])} if (J / "manifest.json").exists() else {}
assembled = {}
for an, r in asr.items():
    base = [{k: w[k] for k in ("word", "start", "end", "score") if k in w} for w in r["words"]]
    if an == "live":
        lw = [dict(w, speaker=orig.get("live_speaker")) for w, orig in zip(base, r["words"])]
        assembled["live|live"] = lw
        st = speaker_stats(lw); st["anchors"] = anchor_report(lw, "live"); st["names"] = mapping
        score["assemblies"]["live|live"] = st
    for tn, turns in turnsets.items():
        key = f"{an}|{tn}"
        if an == "live" and tn in ("sortformer-production",):
            pass
        aw = assign(base, turns); assembled[key] = aw
        st = speaker_stats(aw); st["anchors"] = anchor_report(aw, tn)
        score["assemblies"][key] = st
        if a.mode == "assemble":
            json.dump({"asr": an, "turns": tn, "words": aw}, open(J / f"assembled-{an}-{tn}.json", "w"), ensure_ascii=False)
            (J / f"assembled-{an}-{tn}.md").write_text(to_md(aw))
# agreement matrix for the live ASR words across diarizers (identity comparison on identical words)
ref_asr = "live" if "live" in asr else (names[0] if names else None)
if ref_asr:
    keys = [k for k in assembled if k.startswith(ref_asr + "|")]
    score["diarizer_agreement_on_" + ref_asr] = {f"{k1.split('|')[1]} vs {k2.split('|')[1]}": agreement(assembled[k1], assembled[k2]) for k1, k2 in itertools.combinations(keys, 2)}
json.dump(score, open(J / "score.json", "w"), indent=1, ensure_ascii=False)

print(f"== {J.name}  source {dur}s  VAD kept {score['vad']}")
for n, s in score["asr"].items():
    print(f"  ASR {n:7} lang={s['language']} words={s['words']} speech={s['speech_seconds']}s last={s['last_word_end']}s fillers={s['fillers']} runs={s['repeated_runs_ge8']} aligned={s['aligned']} lowscore={s['low_score_words']} dropped={s['dropped_segments']} t={s['timings']}")
    if s.get("added_spans") is not None:
        print(f"      union added {s['added_words']} words in {len(s['added_spans'])} spans:")
        for q in s["added_spans"][:30]: print(f"        {q['start']/60:6.1f}m-{q['end']/60:.1f}m {q['text'][:90]!r}")
    if s.get("flagged"):
        f = s["flagged"]; print(f"      flagged {f['total']}: {f['likely_silence_filler']} likely filler, {len(f['plausible_quiet_speech'])} plausible quiet speech:")
        for q in f["plausible_quiet_speech"][:30]: print(f"        {q['start']/60:6.1f}m nsp={q['no_speech_prob']} {q['text'][:80]!r}")
for k, v in score["lost_spans"].items():
    if v["spans"]:
        print(f"  LOST {k}: {v['spans']} spans / {v['words']} words; e.g. " + " | ".join(f"{e['start']}-{e['end']} {e['text'][:50]}" for e in v["examples"][:3]))
for k, s in score["assemblies"].items():
    print(f"  ASM {k:34} labels={s['labels']} subst={s['substantive_labels']} secs={s['seconds_by_label']} unlabeled={s['unlabeled_words']}w changes={s['speaker_changes']}")
    for ar in s["anchors"]:
        print(f"      anchor {ar['anchor']}: " + " / ".join(f"[{r['speaker']} {r['start']}-{r['end']}] {r['text'][:40]}" for r in ar["runs"][:8]))
for k, v in score.items():
    if k.startswith("diarizer_agreement"):
        print("  AGREE", k, v)
