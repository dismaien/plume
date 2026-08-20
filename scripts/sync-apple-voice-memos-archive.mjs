#!/usr/bin/env node

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

const args = parseArgs(process.argv.slice(2));
const liveRoot = args.liveRoot || path.join(os.homedir(), "Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings");
const archiveRoot = args.archiveRoot || "/Users/leona/Documents/Voice Notes Archive/10-raw-apple-voice-memos-sync-20260615";
const reportsRoot = args.reportsRoot || "/Users/leona/Documents/Voice Notes Archive/20-quill-processing/archive-sync-reports";
const sinceDate = String(args.sinceDate || "00000000");
const minimumFreeGB = Number(args.minimumFreeGb || 12);
const archiveRecordings = path.join(archiveRoot, "Recordings");
const metadataDir = path.join(archiveRoot, "metadata");
const indexPath = path.join(metadataDir, "recordings-index.tsv");
const checksumsPath = path.join(archiveRoot, "checksums.sha256");
const liveDB = path.join(liveRoot, "CloudRecordings.db");
const runStamp = new Date().toISOString().replace(/:/g, "-");
const reportPath = args.report || path.join(reportsRoot, `archive-sync-${runStamp}.json`);
const snapshotPath = path.join(metadataDir, `CloudRecordings.snapshot-${runStamp}.db`);

for (const required of [liveRoot, archiveRoot, archiveRecordings, metadataDir, reportsRoot, liveDB]) {
  if (!fs.existsSync(required)) throw new Error(`Required path is missing: ${required}`);
}

assertDiskSpace();
const runStartedAt = new Date().toISOString();
const liveAudio = listAudio(liveRoot);
const archiveAudioBefore = listAudio(archiveRecordings);
const archiveNamesBefore = new Set(archiveAudioBefore);
const manifest = readChecksumManifest(checksumsPath);
const manifestPathsByHash = invertManifest(manifest);
const copiedAudio = [];
const copiedSidecars = [];
const changedConflicts = [];
const duplicateContentSkipped = [];

for (const filename of liveAudio) {
  const source = path.join(liveRoot, filename);
  const destination = path.join(archiveRecordings, filename);
  const missing = !fs.existsSync(destination);
  const recent = filename.slice(0, 8) >= sinceDate;
  if (!missing && !recent) continue;

  const sourceHash = sha256File(source);
  if (missing) {
    const duplicatePaths = manifestPathsByHash.get(sourceHash) || [];
    if (duplicatePaths.length) {
      duplicateContentSkipped.push({ filename, sha256: sourceHash, existing_paths: duplicatePaths });
      continue;
    }
    copyVerified(source, destination, sourceHash);
    manifest.set(`Recordings/${filename}`, sourceHash);
    copiedAudio.push(filename);
    copyMatchingSidecars(filename);
    continue;
  }

  const archivedHash = manifest.get(`Recordings/${filename}`) || sha256File(destination);
  if (sourceHash === archivedHash) continue;
  const extension = path.extname(filename);
  const base = path.basename(filename, extension);
  const conflictName = `${base}--conflict-${runStamp}${extension}`;
  const conflictPath = path.join(archiveRecordings, conflictName);
  copyVerified(source, conflictPath, sourceHash);
  manifest.set(`Recordings/${conflictName}`, sourceHash);
  changedConflicts.push({
    original: filename,
    archived_as: conflictName,
    live_size: fs.statSync(source).size,
    archive_size: fs.statSync(destination).size,
  });
}

snapshotDatabase();
manifest.set(path.relative(archiveRoot, snapshotPath), sha256File(snapshotPath));
rebuildMetadataIndex(snapshotPath);
manifest.set(path.relative(archiveRoot, indexPath), sha256File(indexPath));
writeChecksumManifest(checksumsPath, manifest);

const archiveAudioAfter = listAudio(archiveRecordings);
const archiveNamesAfter = new Set(archiveAudioAfter);
const report = {
  run_started_at: runStartedAt,
  run_completed_at: new Date().toISOString(),
  live_root: liveRoot,
  archive_root: archiveRoot,
  live_audio_count: liveAudio.length,
  considered_audio_count: liveAudio.filter((name) => !archiveNamesBefore.has(name) || name.slice(0, 8) >= sinceDate).length,
  since_date: sinceDate,
  archive_audio_before: archiveAudioBefore.length,
  archive_audio_after: archiveAudioAfter.length,
  copied_audio_count: copiedAudio.length,
  copied_audio_bytes: copiedAudio.reduce((total, name) => total + fs.statSync(path.join(archiveRecordings, name)).size, 0),
  copied_sidecar_count: copiedSidecars.length,
  changed_conflicts_count: changedConflicts.length,
  duplicate_content_skipped_count: duplicateContentSkipped.length,
  copied_audio: copiedAudio,
  copied_sidecars: copiedSidecars,
  changed_conflicts: changedConflicts,
  duplicate_content_skipped: duplicateContentSkipped,
  dated_snapshot_path: snapshotPath,
  index_path: indexPath,
  checksums_path: checksumsPath,
  live_audio_not_in_archive_after: liveAudio.filter((name) => !archiveNamesAfter.has(name)),
  archive_audio_not_in_live: archiveAudioAfter.filter((name) => !new Set(liveAudio).has(name)),
};

atomicWrite(reportPath, `${JSON.stringify(report, null, 2)}\n`);
console.log(JSON.stringify({ event: "archive_sync_completed", report: reportPath, copied_audio: copiedAudio.length, copied_sidecars: copiedSidecars.length, conflicts: changedConflicts.length }));

function copyMatchingSidecars(audioFilename) {
  const stem = path.basename(audioFilename, path.extname(audioFilename));
  for (const entry of fs.readdirSync(liveRoot, { withFileTypes: true })) {
    if (!entry.isFile() || entry.name === audioFilename || !entry.name.startsWith(`${stem}-`)) continue;
    const source = path.join(liveRoot, entry.name);
    const destination = path.join(archiveRecordings, entry.name);
    if (fs.existsSync(destination)) continue;
    const hash = sha256File(source);
    copyVerified(source, destination, hash);
    manifest.set(`Recordings/${entry.name}`, hash);
    copiedSidecars.push(entry.name);
  }
}

function snapshotDatabase() {
  const temp = `${snapshotPath}.partial-${process.pid}`;
  fs.rmSync(temp, { force: true });
  execFileSync("sqlite3", [liveDB, `.backup '${temp.replaceAll("'", "''")}'`], { timeout: 120_000 });
  fs.renameSync(temp, snapshotPath);
}

function rebuildMetadataIndex(databasePath) {
  const query = `
select
  Z_PK as pk,
  coalesce(ZUNIQUEID, '') as unique_id,
  coalesce(ZPATH, '') as apple_path,
  coalesce(nullif(ZENCRYPTEDTITLE, ''), nullif(ZCUSTOMLABELFORSORTING, ''), '') as apple_title,
  coalesce(ZCUSTOMLABEL, '') as apple_custom_label,
  case when ZDATE is null then '' else strftime('%Y-%m-%d %H:%M:%SZ', ZDATE + 978307200, 'unixepoch') end as recorded_at_utc,
  coalesce(ZDATE, '') as apple_coredata_seconds,
  coalesce(nullif(ZLOCALDURATION, 0), ZDURATION, 0) as duration_seconds,
  coalesce(ZFOLDER, '') as folder_id
from ZCLOUDRECORDING
order by ZDATE, Z_PK;`;
  const rows = JSON.parse(execFileSync("sqlite3", ["-json", databasePath, query], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }) || "[]");
  const headers = ["pk", "unique_id", "apple_path", "apple_title", "apple_custom_label", "recorded_at_utc", "apple_coredata_seconds", "duration_seconds", "folder_id"];
  const lines = [headers.join("\t"), ...rows.map((row) => headers.map((header) => tsvCell(row[header])).join("\t"))];
  atomicWrite(indexPath, `${lines.join("\n")}\n`);
}

function copyVerified(source, destination, expectedHash) {
  assertDiskSpace(fs.statSync(source).size);
  const temp = `${destination}.partial-${process.pid}`;
  fs.rmSync(temp, { force: true });
  fs.copyFileSync(source, temp);
  const actualHash = sha256File(temp);
  if (actualHash !== expectedHash) {
    fs.rmSync(temp, { force: true });
    throw new Error(`Checksum mismatch while copying ${source}`);
  }
  fs.renameSync(temp, destination);
}

function assertDiskSpace(additionalBytes = 0) {
  const output = execFileSync("df", ["-Pk", "/"], { encoding: "utf8" }).trim().split(/\r?\n/).at(-1);
  const availableKB = Number(output.trim().split(/\s+/)[3]);
  const freeGB = (availableKB * 1024 - additionalBytes) / 1024 / 1024 / 1024;
  if (!Number.isFinite(freeGB) || freeGB < minimumFreeGB) {
    throw new Error(`Copy would leave ${freeGB.toFixed(1)} GB free, below the ${minimumFreeGB} GB floor`);
  }
}

function listAudio(directory) {
  return fs.readdirSync(directory, { withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.(?:m4a|qta)$/i.test(entry.name))
    .map((entry) => entry.name)
    .sort();
}

function sha256File(file) {
  const hash = crypto.createHash("sha256");
  const descriptor = fs.openSync(file, "r");
  const buffer = Buffer.allocUnsafe(4 * 1024 * 1024);
  try {
    for (;;) {
      const bytes = fs.readSync(descriptor, buffer, 0, buffer.length, null);
      if (!bytes) break;
      hash.update(buffer.subarray(0, bytes));
    }
  } finally {
    fs.closeSync(descriptor);
  }
  return hash.digest("hex");
}

function readChecksumManifest(file) {
  const entries = new Map();
  if (!fs.existsSync(file)) return entries;
  for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const match = line.match(/^([a-f0-9]{64})\s{2}(.+)$/i);
    if (match) entries.set(match[2], match[1].toLowerCase());
  }
  return entries;
}

function invertManifest(manifest) {
  const paths = new Map();
  for (const [file, hash] of manifest) paths.set(hash, [...(paths.get(hash) || []), file]);
  return paths;
}

function writeChecksumManifest(file, manifest) {
  const text = [...manifest.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, hash]) => `${hash}  ${name}`)
    .join("\n");
  atomicWrite(file, `${text}\n`);
}

function atomicWrite(file, contents) {
  const temp = `${file}.partial-${process.pid}`;
  fs.writeFileSync(temp, contents);
  fs.renameSync(temp, file);
}

function tsvCell(value) {
  return String(value ?? "").replace(/[\t\r\n]+/g, " ").trim();
}

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (!argument.startsWith("--")) continue;
    const key = argument.slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    const next = argv[index + 1];
    if (!next || next.startsWith("--")) parsed[key] = true;
    else {
      parsed[key] = next;
      index += 1;
    }
  }
  return parsed;
}
