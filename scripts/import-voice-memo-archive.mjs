#!/usr/bin/env node

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

const args = parseArgs(process.argv.slice(2));
const archiveRoot = args.archiveRoot || "/Users/leona/Documents/Voice Notes Archive/10-raw-apple-voice-memos-sync-20260615";
const recordingsDir = path.join(archiveRoot, "Recordings");
const indexPath = path.join(archiveRoot, "metadata", "recordings-index.tsv");
const checksumsPath = path.join(archiveRoot, "checksums.sha256");
const dbPath = args.db || "/Users/leona/Library/Application Support/plume-desktop/data/quill.db";
const apiBase = args.apiBase || "http://127.0.0.1:49291/api/v1";
const syncReportPath = requiredArg("syncReport");
const statePath = args.state || path.join(path.dirname(syncReportPath), `plume-import-${path.basename(syncReportPath, ".json")}.json`);
const shouldStart = Boolean(args.start || args.monitor);
const shouldMonitor = Boolean(args.monitor);

const syncReport = JSON.parse(fs.readFileSync(syncReportPath, "utf8"));
const copiedNames = [...new Set(syncReport.copied_audio || [])].filter(isAudioFilename);
const index = readTSVIndex(indexPath);
const manifestHashes = readChecksumManifest(checksumsPath);
const activeVaultID = Number(sqliteRows("select id from vaults where is_active = 1 order by id limit 1;")[0]?.id || 0);

if (!activeVaultID) throw new Error("Plume has no active vault");
if (!copiedNames.length) throw new Error(`Sync report contains no copied audio: ${syncReportPath}`);

const jobs = [];
for (const filename of copiedNames) {
  const audioPath = path.join(recordingsDir, filename);
  if (!fs.existsSync(audioPath)) throw new Error(`Archived audio is missing: ${audioPath}`);

  const relativePath = `Recordings/${filename}`;
  const expectedHash = manifestHashes.get(relativePath);
  if (!expectedHash) throw new Error(`Checksum manifest has no entry for ${relativePath}`);
  const actualHash = sha256File(audioPath);
  if (actualHash !== expectedHash) throw new Error(`Checksum mismatch for ${relativePath}`);

  const metadata = index.get(filename) || {};
  const existing = sqliteRows(`select id, status, title, audio_path from transcription_jobs where file_hash = ${quote(actualHash)} and deleted_at is null limit 1;`)[0];
  const id = existing?.id || `archive-${actualHash.slice(0, 32)}`;
  const appleTitle = cleanText(metadata.apple_title) || path.parse(filename).name;
  const recordedAt = filenameRecordedAt(filename);

  if (!existing) {
    const now = new Date().toISOString();
    sqliteRun(`begin immediate;
insert into transcription_jobs (
  id, title, status, audio_path, vault_id, diarization, merge_status,
  created_at, updated_at, model_family, model, device, batch_size,
  compute_type, output_format, verbose, task, interpolate_method,
  vad_preprocess, vad_method, vad_onset, vad_offset, chunk_size,
  diarize, diarize_model, temperature, best_of, beam_size, patience,
  length_penalty, condition_on_previous_text, fp16,
  temperature_increment_on_fallback, compression_ratio_threshold,
  logprob_threshold, no_speech_threshold, segment_resolution,
  original_filename, file_hash, recorded_at
) values (
  ${quote(id)}, ${quote(appleTitle)}, 'uploaded', ${quote(audioPath)}, ${activeVaultID}, 1, 'none',
  ${quote(now)}, ${quote(now)}, 'whisper', 'large-v3', 'cpu', 8,
  'int8', 'all', 1, 'transcribe', 'nearest',
  0, 'silero', 0.5, 0.363, 30,
  1, 'sherpa-onnx', 0, 5, 5, 1,
  1, 0, 1,
  0.2, 2.4,
  -1, 0.6, 'sentence',
  ${quote(filename)}, ${quote(actualHash)}, ${quote(recordedAt)}
);
commit;`);
  }

  jobs.push({
    id,
    filename,
    audio_path: audioPath,
    file_hash: actualHash,
    apple_title: appleTitle,
    recorded_at_local: recordedAt,
    existed: Boolean(existing),
    initial_status: existing?.status || "uploaded",
  });
}

// Recent archive syncs are most useful newest-first. This also gives an
// explicitly requested latest memo priority over older files in the same batch.
jobs.sort((left, right) => right.filename.localeCompare(left.filename));

writeState({
  created_at: new Date().toISOString(),
  sync_report: syncReportPath,
  archive_root: archiveRoot,
  storage: "reference",
  model: "WhisperX large-v3 CPU int8 with Silero VAD",
  jobs,
});

console.log(JSON.stringify({ event: "prepared", jobs: jobs.length, inserted: jobs.filter((job) => !job.existed).length, state: statePath }));

if (shouldStart) {
  requireReferenceBackend();
  for (const job of jobs) {
    const current = getJob(job.id);
    if (["uploaded", "failed"].includes(current.status)) {
      const started = requestJSON("POST", `${apiBase}/transcription/${job.id}/start`, qualityParameters());
      console.log(JSON.stringify({ event: "queued", id: job.id, status: started.status, filename: job.filename }));
    } else {
      console.log(JSON.stringify({ event: "already_active_or_complete", id: job.id, status: current.status }));
    }
  }
}

if (shouldMonitor) {
  const finished = new Set();
  while (finished.size < jobs.length) {
    for (const job of jobs) {
      if (finished.has(job.id)) continue;
      const current = getJob(job.id);
      if (current.status === "completed") {
        if (job.existed && job.initial_status === "completed") {
          finished.add(job.id);
          console.log(JSON.stringify({ event: "existing_completed", id: job.id, title: current.title }));
          continue;
        }
        try {
          let titled = requestJSON("POST", `${apiBase}/transcription/${job.id}/title/auto?force=true`);
          if (isLocationTitle(job.apple_title) && typeof titled.title === "string") {
            const corrected = moveLocationToSuffix(titled.title, job.apple_title);
            if (corrected !== titled.title) {
              titled = requestJSON("PUT", `${apiBase}/transcription/${job.id}/title`, { title: corrected });
            }
          }
          console.log(JSON.stringify({ event: "completed_and_titled", id: job.id, title: titled.title || current.title }));
        } catch (error) {
          console.error(JSON.stringify({ event: "completed_title_failed", id: job.id, title: current.title, error: String(error?.message || error) }));
        }
        finished.add(job.id);
      } else if (["cancelled"].includes(current.status)) {
        finished.add(job.id);
        console.log(JSON.stringify({ event: "terminal", id: job.id, status: current.status }));
      } else if (current.status === "failed") {
        console.log(JSON.stringify({ event: "failed_waiting_for_retry", id: job.id, error: current.error_message || null }));
      }
    }
    writeState({
      updated_at: new Date().toISOString(),
      sync_report: syncReportPath,
      archive_root: archiveRoot,
      storage: "reference",
      model: "WhisperX large-v3 CPU int8 with Silero VAD",
      completed: finished.size,
      jobs: jobs.map((job) => ({ ...job, status: safeJobStatus(job.id) })),
    });
    if (finished.size < jobs.length) await sleep(30_000);
  }
}

function qualityParameters() {
  return {
    model_family: "whisper",
    model: "large-v3",
    model_cache_only: false,
    device: "cpu",
    device_index: 0,
    batch_size: 8,
    compute_type: "int8",
    threads: 0,
    output_format: "all",
    verbose: true,
    task: "transcribe",
    interpolate_method: "nearest",
    no_align: false,
    return_char_alignments: false,
    vad_preprocess: false,
    vad_method: "silero",
    vad_onset: 0.5,
    vad_offset: 0.363,
    chunk_size: 30,
    diarize: true,
    diarize_model: "sherpa-onnx",
    speaker_embeddings: false,
    temperature: 0,
    best_of: 5,
    beam_size: 5,
    patience: 1,
    length_penalty: 1,
    suppress_numerals: false,
    condition_on_previous_text: false,
    fp16: true,
    temperature_increment_on_fallback: 0.2,
    compression_ratio_threshold: 2.4,
    logprob_threshold: -1,
    no_speech_threshold: 0.6,
    highlight_words: false,
    segment_resolution: "sentence",
    print_progress: false,
    is_multi_track_enabled: false,
  };
}

function requireReferenceBackend() {
  const pids = execFileSync("pgrep", ["-f", "/Applications/Plume.app/Contents/Resources/backend/quill"], { encoding: "utf8" })
    .trim().split(/\s+/).filter(Boolean);
  if (!pids.length) throw new Error("Plume backend is not running");
  const command = execFileSync("ps", ["eww", "-p", pids[0], "-o", "command="], { encoding: "utf8" });
  if (!/(?:PLUME_)?AUDIO_STORAGE_MODE=reference/.test(command)) {
    throw new Error("Refusing to start: Plume backend is not running in reference storage mode");
  }
}

function getJob(id) {
  return requestJSON("GET", `${apiBase}/transcription/${id}/status`);
}

function safeJobStatus(id) {
  try {
    return getJob(id).status;
  } catch {
    return "unavailable";
  }
}

function requestJSON(method, url, body) {
  const curlArgs = ["-sS", "--fail-with-body", "-X", method, url];
  let input;
  if (body !== undefined) {
    curlArgs.push("-H", "Content-Type: application/json", "--data-binary", "@-");
    input = JSON.stringify(body);
  }
  const output = execFileSync("curl", curlArgs, { encoding: "utf8", input, timeout: 180_000, maxBuffer: 64 * 1024 * 1024 });
  return output.trim() ? JSON.parse(output) : {};
}

function readTSVIndex(file) {
  const lines = fs.readFileSync(file, "utf8").split(/\r?\n/).filter(Boolean);
  const headers = lines.shift().split("\t");
  return new Map(lines.map((line) => {
    const values = line.split("\t");
    const row = Object.fromEntries(headers.map((header, index) => [header, values[index] || ""]));
    return [row.apple_path, row];
  }));
}

function readChecksumManifest(file) {
  const entries = new Map();
  for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const match = line.match(/^([a-f0-9]{64})\s{2}(.+)$/i);
    if (match) entries.set(match[2], match[1].toLowerCase());
  }
  return entries;
}

function sha256File(file) {
  const hash = crypto.createHash("sha256");
  const fd = fs.openSync(file, "r");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    for (;;) {
      const bytes = fs.readSync(fd, buffer, 0, buffer.length, null);
      if (!bytes) break;
      hash.update(buffer.subarray(0, bytes));
    }
  } finally {
    fs.closeSync(fd);
  }
  return hash.digest("hex");
}

function filenameRecordedAt(filename) {
  const match = filename.match(/^(\d{4})(\d{2})(\d{2}) (\d{2})(\d{2})(\d{2})(?:-|\.|$)/);
  if (!match) return "";
  return `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]}:${match[6]}`;
}

function isLocationTitle(title) {
  return /^(?:Rua|R.|Praça|Praca|Avenida|Av\.|Calle|Carrer|Street|St\.|Road|Rd\.|Beco|Jalan|Fraunhoferstra|Bristlestra|Maspeth|Millennium|Schwarzwald)/i.test(title.trim());
}

function moveLocationToSuffix(title, location) {
  const escaped = escapeRegExp(location.trim());
  let result = title.replace(new RegExp(`\\s*\\(${escaped}\\)`, "i"), "").trim();
  if (new RegExp(`@${escaped}`, "i").test(result)) return result;
  const duration = result.match(/\s+(\{\d{2}:\d{2}\})$/);
  if (duration) {
    result = `${result.slice(0, -duration[0].length)} @${location.trim()} ${duration[1]}`;
  } else {
    result = `${result} @${location.trim()}`;
  }
  return result.replace(/\s+/g, " ").trim();
}

function sqliteRows(sql) {
  const output = execFileSync("sqlite3", ["-json", dbPath, sql], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  return output.trim() ? JSON.parse(output) : [];
}

function sqliteRun(sql) {
  execFileSync("sqlite3", [dbPath, sql], { encoding: "utf8", timeout: 120_000 });
}

function writeState(value) {
  fs.mkdirSync(path.dirname(statePath), { recursive: true });
  const temp = `${statePath}.tmp`;
  fs.writeFileSync(temp, `${JSON.stringify(value, null, 2)}\n`);
  fs.renameSync(temp, statePath);
}

function quote(value) {
  if (value === null || value === undefined || value === "") return "NULL";
  return `'${String(value).replaceAll("'", "''")}'`;
}

function cleanText(value) {
  return String(value || "").trim();
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function isAudioFilename(filename) {
  return /\.(?:m4a|qta|mp3|wav|flac|aac|ogg|aiff)$/i.test(filename);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function requiredArg(name) {
  const value = args[name];
  if (!value) throw new Error(`Missing required --${name}`);
  return value;
}

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (!argument.startsWith("--")) continue;
    const key = argument.slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    const next = argv[index + 1];
    if (!next || next.startsWith("--")) {
      parsed[key] = true;
    } else {
      parsed[key] = next;
      index += 1;
    }
  }
  return parsed;
}
