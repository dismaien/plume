#!/usr/bin/env python3
"""Historical diagnostic, not production: speaker reclustering over embeddings.

Its duration/share identity rule is obsolete. Do not use it for accuracy
selection; public_benchmark.py invokes the actual current production code.
Input: emb-*.json {turns:[{start,end,speaker}], embeddings:[[..]], model}.
Output: clusters per turn with a threshold sweep, separation metrics, purity of
the original diarizer labels against the clusters, and uncertainty flags."""
import json, sys, itertools
import numpy as np
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import pdist, squareform
from sklearn.metrics import silhouette_score

inp, out = sys.argv[1:3]
d = json.load(open(inp))
turns, E = d["turns"], np.array(d["embeddings"], dtype=np.float64)
res = {"source": inp, "embedding_model": d.get("model"), "n_turns": len(turns), "sweep": []}
if len(turns) < 3:
    res["note"] = "too few turns to cluster"; json.dump(res, open(out, "w"), indent=1); sys.exit(0)
E = E / np.clip(np.linalg.norm(E, axis=1, keepdims=True), 1e-9, None)
dur = np.array([t["end"] - t["start"] for t in turns])
D = pdist(E, metric="cosine")
Z = linkage(D, method="average")
DM = squareform(D)
best = None
for thr in [0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8]:
    labels = fcluster(Z, t=thr, criterion="distance")
    k = len(set(labels))
    sil = float(silhouette_score(DM, labels, metric="precomputed")) if 1 < k < len(turns) else None
    secs = {}
    for l, s in zip(labels, dur):
        secs[int(l)] = secs.get(int(l), 0) + float(s)
    res["sweep"].append({"threshold": thr, "clusters": k, "silhouette": sil,
                         "seconds_by_cluster": dict(sorted(secs.items(), key=lambda x: -x[1]))})
    if sil is not None and (best is None or sil > best[0]):
        best = (sil, thr, labels)
if best is None:
    thr, labels = 0.5, fcluster(Z, t=0.5, criterion="distance")
else:
    _, thr, labels = best
labels = [int(l) for l in labels]
# v2: identities are clusters carrying substantive speech; fragments are
# reassigned to the nearest identity centroid by voice distance (never by time)
# and flagged uncertain when the margin is small or the turn is short.
total_speech = float(dur.sum())
cl_secs = {}
for l, s in zip(labels, dur):
    cl_secs[l] = cl_secs.get(l, 0.0) + float(s)
min_identity = max(20.0, 0.03 * total_speech)
identities = [l for l, s in sorted(cl_secs.items(), key=lambda x: -x[1]) if s >= min_identity][:6] or [max(cl_secs, key=cl_secs.get)]
cents = {}
for l in identities:
    idx = [i for i, x in enumerate(labels) if x == l]
    c = (E[idx] * dur[idx, None]).sum(0); cents[l] = c / max(np.linalg.norm(c), 1e-9)
assigned = []
for i, t in enumerate(turns):
    dists = sorted((1 - float(E[i] @ cents[l]), l) for l in cents)
    margin = (dists[1][0] - dists[0][0]) if len(dists) > 1 else 1.0
    was_fragment = labels[i] not in cents
    far = dists[0][0] > 0.45
    assigned.append({**t, "cluster": dists[0][1], "raw_cluster": labels[i], "fragment_reassigned": was_fragment,
                     "nearest_distance": round(dists[0][0], 4), "margin": round(margin, 4),
                     "uncertain": bool(margin < 0.05 or dur[i] < 1.0 or far)})
res["identity_rule"] = {"min_identity_seconds": round(min_identity, 1), "raw_clusters_at_threshold": len(cl_secs),
                        "identities": len(cents), "fragments_reassigned": sum(a["fragment_reassigned"] for a in assigned)}
# purity: how original diarizer labels distribute over clusters
cont = {}
for t in assigned:
    cont.setdefault(t["speaker"], {}).setdefault(t["cluster"], 0.0)
    cont[t["speaker"]][t["cluster"]] += t["end"] - t["start"]
purity = {spk: {"seconds": round(sum(v.values()), 1),
                "dominant_cluster_share": round(max(v.values()) / sum(v.values()), 3),
                "by_cluster": {str(k): round(s, 1) for k, s in sorted(v.items(), key=lambda x: -x[1])}}
          for spk, v in cont.items()}
cl_secs = {}
for t in assigned:
    cl_secs[t["cluster"]] = cl_secs.get(t["cluster"], 0) + t["end"] - t["start"]
res.update(chosen_threshold=thr, chosen_clusters=len(cents), chosen_silhouette=best[0] if best else None,
           seconds_by_cluster={str(k): round(v, 1) for k, v in sorted(cl_secs.items(), key=lambda x: -x[1])},
           uncertain_turns=sum(t["uncertain"] for t in assigned),
           uncertain_seconds=round(sum(t["end"] - t["start"] for t in assigned if t["uncertain"]), 1),
           original_label_purity=purity, turns=assigned)
json.dump(res, open(out, "w"), indent=1)
print(f"recluster {d.get('model')}: {len(cents)} clusters at thr {thr} sil={res['chosen_silhouette']}; seconds {res['seconds_by_cluster']}")
print("original label purity:", {k: v["dominant_cluster_share"] for k, v in purity.items()})
