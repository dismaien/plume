#!/usr/bin/env python3
"""No-write canary evaluation harness for Plume transcription quality.

Reads the live database read-only, verifies source audio against the archive
checksum manifest, and runs candidate pipeline stages as subprocesses in the
managed runtime environments. Every stage writes immutable JSON under the
private output root. It never calls Plume's API and never writes under the
backend data directory.

Usage:
  harness.py --job archive-... [--job ...] [--stages decode,vad,asr:silero,...]
             [--out ROOT] [--force] [--anchors anchors.json]
"""
import argparse, fcntl, hashlib, json, os, shutil, sqlite3, subprocess, sys, time
from pathlib import Path

HOME = Path.home()
DATA = HOME / "Library/Application Support/plume-desktop/data"
DB = DATA / "quill.db"
ENV = DATA / "whisperx-env"
ARCHIVE = HOME / "Documents/Voice Notes Archive/10-raw-apple-voice-memos-sync-20260615"
DEFAULT_OUT = HOME / "Documents/Voice Notes Archive/20-quill-processing/quality-eval-20261011"
TOOLS = Path("/Applications/Plume.app/Contents/Resources/tools")
FFMPEG, FFPROBE = TOOLS / "ffmpeg", TOOLS / "ffprobe"
STAGES_DIR = Path(__file__).resolve().parent
PY = {
    "whisperx": ENV / "WhisperX/.venv/bin/python",
    "parakeet": ENV / "parakeet/.venv/bin/python",
    "silero": ENV / "silero-vad/.venv/bin/python",
    "system": Path(sys.executable),
}
ALL_STAGES = ["decode", "vad", "asr:silero", "asr:full", "asr:hybrid", "diar:sortformer",
              "diar:pyannote", "embed", "recluster", "assemble", "score"]
FLOOR_BYTES = 12 * 1024 ** 3


class Pause(Exception):
    pass

class StageFailure(Exception):
    pass


def sha256(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def db_ro():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def guard(out_root):
    if (out_root / "STOP").exists():
        raise Pause("STOP file present")
    for p in (out_root, DATA):
        if shutil.disk_usage(p).free < FLOOR_BYTES:
            raise Pause(f"free space below 12 GiB at {p}")
    with db_ro() as db:
        n = db.execute("select count(*) from transcription_jobs where status in ('pending','processing')").fetchone()[0]
    if n:
        raise Pause(f"Plume has {n} active job(s); yielding CPU")


def manifest_hash(audio_path):
    rel = "Recordings/" + Path(audio_path).name
    for line in (ARCHIVE / "checksums.sha256").read_text().splitlines():
        if line.endswith("  " + rel):
            return line.split()[0].lower()
    return None


def job_row(job_id):
    with db_ro() as db:
        db.row_factory = sqlite3.Row
        row = db.execute("select id,title,status,audio_path,audio_duration_ms,file_hash,language,vad_preprocess,"
                         "whisper_full_audio,diarize_model,transcript_json_path from transcription_jobs where id=?",
                         (job_id,)).fetchone()
        if not row:
            raise SystemExit(f"job not found: {job_id}")
        maps = db.execute("select original_speaker,custom_name,match_source from speaker_mappings where transcription_job_id=?",
                          (job_id,)).fetchall()
    return dict(row), [dict(m) for m in maps]


def base_env(extra=None):
    env = dict(os.environ)
    env.update({
        "HF_HOME": str(ENV / "cache/huggingface"),
        "HUGGINGFACE_HUB_CACHE": str(ENV / "cache/huggingface/hub"),
        "TORCH_HOME": str(ENV / "cache/torch"),
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
        "OMP_NUM_THREADS": "6", "TOKENIZERS_PARALLELISM": "false", "PYTHONUNBUFFERED": "1",
    })
    if extra:
        env.update(extra)
    return env


def run_stage(jobdir, name, python, script, args, env_extra=None, manifest=None):
    log = jobdir / "logs" / (name.replace(":", "-") + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(python), "-u", str(STAGES_DIR / script)] + [str(a) for a in args]
    t0 = time.time()
    started = now()
    with open(log, "a") as lf:
        lf.write(f"\n===== {now()} {name}\n{' '.join(cmd)}\n")
        lf.flush()
        proc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, env=base_env(env_extra), cwd=str(jobdir))
    dt = time.time() - t0
    rec = {"stage": name, "started": started, "seconds": round(dt, 1), "returncode": proc.returncode, "log": str(log)}
    manifest.setdefault("stages", []).append(rec)
    if proc.returncode:
        manifest['updated'] = now()
        (jobdir / 'manifest.json').write_text(json.dumps(manifest,indent=1,default=str))
        raise StageFailure(f'{name} exited {proc.returncode}; see {log}')
    return True, dt


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--job", action="append", required=True)
    ap.add_argument("--stages", default=",".join(ALL_STAGES))
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true", help="re-run stages whose outputs exist")
    ap.add_argument("--anchors", type=Path, help="JSON {job_id: [[start,end,label],...]}")
    ap.add_argument("--full-nst", default="0.5", help="no_speech_threshold for the full-audio profile")
    ap.add_argument("--num-speakers", type=int, default=0, help="also run Pyannote with an exact speaker count")
    a = ap.parse_args()
    stages = [s for s in a.stages.split(",") if s]
    a.out.mkdir(parents=True, exist_ok=True)
    anchors = json.loads(a.anchors.read_text()) if a.anchors else {}
    with open(a.out / ".harness.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit("another harness instance holds the lock")
        for job_id in a.job:
            try:
                process_job(job_id, stages, a, anchors.get(job_id, []))
            except StageFailure as e:
                print(f"FAILED {job_id}: {e}")
                return 1
            except Pause as e:
                print(f"PAUSED before finishing {job_id}: {e}")
                return 2
    return 0


def process_job(job_id, stages, a, anchors):
    jobdir = a.out / job_id
    jobdir.mkdir(parents=True, exist_ok=True)
    mpath = jobdir / "manifest.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else {"job_id": job_id, "created": now(), "stages": []}
    row, maps = job_row(job_id)
    src = Path(row["audio_path"])
    expected = manifest_hash(src) or row["file_hash"]
    actual = sha256(src)
    if expected and actual != expected:
        raise SystemExit(f"source hash mismatch for {job_id}: {actual} != {expected}")
    manifest.update(job=row, speaker_mappings=maps, source_sha256=actual, source_bytes=src.stat().st_size)
    wav = jobdir / "source-16k.wav"

    def save():
        manifest["updated"] = now()
        tmp = mpath.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, indent=1, default=str))
        tmp.replace(mpath)

    def done(name, *outputs):
        return (not a.force) and all((jobdir / o).exists() for o in outputs)

    for stage in stages:
        guard(a.out)
        if sha256(src) != actual:
            raise SystemExit("source audio changed during evaluation; stopping")
        print(f"[{job_id[:13]}] {stage} ...", flush=True)
        t0 = time.time()
        if stage == "decode":
            if done(stage, "source-16k.wav", "source.json"):
                print("  skip (exists)"); continue
            dur = subprocess.run([str(FFPROBE), "-v", "error", "-show_entries", "format=duration", "-of",
                                  "default=nw=1:nk=1", str(src)], capture_output=True, text=True).stdout.strip()
            subprocess.run([str(FFMPEG), "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "16000",
                            "-c:a", "pcm_s16le", str(wav)], check=True)
            info = {"ffprobe_seconds": float(dur), "db_duration_ms": row["audio_duration_ms"],
                    "wav_bytes": wav.stat().st_size, "wav_seconds": (wav.stat().st_size - 44) / 32000,
                    "source_sha256": actual, "decoded_at": now()}
            (jobdir / "source.json").write_text(json.dumps(info, indent=1))
            manifest["stages"].append({"stage": stage, "seconds": round(time.time() - t0, 1), "returncode": 0})
        elif stage == "vad":
            if done(stage, "vad-plume-silero.json"):
                print("  skip (exists)"); continue
            ok, _ = run_stage(jobdir, stage, PY["silero"], "stage_vad_silero.py",
                              [wav, jobdir / "vad-plume-silero.json", jobdir / "vad-stripped.wav"], manifest=manifest)
        elif stage.startswith("asr:"):
            mode = stage.split(":")[1]
            out = jobdir / f"asr-{mode}.json"
            if done(stage, out.name):
                print("  skip (exists)"); continue
            args = ["--mode", mode, "--audio", wav, "--out", out, "--manifest", jobdir / "vad-plume-silero.json",
                    "--stripped", jobdir / "vad-stripped.wav", "--full-nst", a.full_nst]
            if mode == "hybrid" and (jobdir / "asr-full.json").exists():
                args += ["--reuse-full", jobdir / "asr-full.json"]
            ok, _ = run_stage(jobdir, stage, PY["whisperx"], "stage_asr_whisperx.py", args, manifest=manifest)
        elif stage == "diar:sortformer":
            if done(stage, "diar-sortformer-raw.json"):
                print("  skip (exists)"); continue
            ok, _ = run_stage(jobdir, stage, PY["parakeet"], "stage_diar_sortformer.py",
                              [wav, jobdir / "diar-sortformer-raw.json", jobdir / "diar-sortformer-production.json"],
                              env_extra={"VIRTUAL_ENV": str(ENV / "parakeet/.venv")}, manifest=manifest)
        elif stage == "diar:pyannote":
            if done(stage, "diar-pyannote.json"):
                print("  skip (exists)"); continue
            args = [wav, jobdir / "diar-pyannote.json"]
            if a.num_speakers:
                args += ["--num-speakers", a.num_speakers, "--exact-out", jobdir / f"diar-pyannote-exact{a.num_speakers}.json"]
            ok, _ = run_stage(jobdir, stage, PY["whisperx"], "stage_diar_pyannote.py", args, manifest=manifest)
        elif stage == "embed":
            # Embed turns from each available diarizer with both embedding models.
            for turns in ("diar-sortformer-raw.json", "diar-pyannote.json"):
                if not (jobdir / turns).exists():
                    continue
                tag = turns.replace("diar-", "").replace(".json", "")
                t_out = jobdir / f"emb-titanet-{tag}.json"
                if not done(stage, t_out.name):
                    run_stage(jobdir, "embed:titanet:" + tag, PY["parakeet"], "stage_diar_sortformer.py",
                              [wav, "--embed-only", jobdir / turns, t_out],
                              env_extra={"VIRTUAL_ENV": str(ENV / "parakeet/.venv")}, manifest=manifest)
                p_out = jobdir / f"emb-pyannote-{tag}.json"
                if not done(stage, p_out.name):
                    run_stage(jobdir, "embed:pyannote:" + tag, PY["whisperx"], "stage_diar_pyannote.py",
                              [wav, "--embed-only", jobdir / turns, p_out], manifest=manifest)
        elif stage == "recluster":
            for emb in sorted(jobdir.glob("emb-*.json")):
                out = jobdir / emb.name.replace("emb-", "recluster-")
                if done(stage, out.name):
                    continue
                run_stage(jobdir, "recluster:" + emb.stem, PY["whisperx"], "stage_recluster.py", [emb, out], manifest=manifest)
        elif stage in ("assemble", "score"):
            args = ["--jobdir", jobdir, "--anchors", json.dumps(anchors), "--mode", stage]
            if row.get("transcript_json_path") and Path(row["transcript_json_path"]).exists():
                args += ["--live", row["transcript_json_path"]]
            run_stage(jobdir, stage, PY["system"], "stage_assemble_score.py", args, manifest=manifest)
        else:
            raise StageFailure(f"unknown stage {stage}")
        save()
        print(f"  {stage} finished in {time.time() - t0:.0f}s", flush=True)
    save()


if __name__ == "__main__":
    sys.exit(main())
