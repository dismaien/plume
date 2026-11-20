#!/usr/bin/env node

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

const dbPath = process.argv[2] || "/Users/leona/Library/Application Support/plume-desktop/data/quill.db";
const linkDir = process.argv[3] || path.join(path.dirname(dbPath), "playback-links");
const rows = JSON.parse(execFileSync("sqlite3", [
  "-json",
  dbPath,
  "select distinct audio_path from transcription_jobs where deleted_at is null and trim(audio_path) != '';",
], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }) || "[]");

fs.mkdirSync(linkDir, { recursive: true });
const totals = { created: 0, existing: 0, missing: 0, unsupported: 0 };

for (const row of rows) {
  const source = String(row.audio_path || "").trim();
  let sourceStat;
  try {
    sourceStat = fs.statSync(source);
  } catch {
    totals.missing += 1;
    continue;
  }
  if (!sourceStat.isFile()) {
    totals.unsupported += 1;
    continue;
  }

  const digest = crypto.createHash("sha256").update(source).digest("hex");
  const linkPath = path.join(linkDir, `${digest}${path.extname(source).toLowerCase()}`);
  try {
    const linkStat = fs.statSync(linkPath);
    if (sourceStat.dev === linkStat.dev && sourceStat.ino === linkStat.ino) {
      totals.existing += 1;
      continue;
    }
    totals.unsupported += 1;
    continue;
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }

  try {
    fs.linkSync(source, linkPath);
    totals.created += 1;
  } catch (error) {
    if (error?.code === "EXDEV" || error?.code === "EPERM" || error?.code === "EACCES") {
      totals.unsupported += 1;
      continue;
    }
    throw error;
  }
}

console.log(JSON.stringify({ database: dbPath, link_dir: linkDir, total: rows.length, ...totals }, null, 2));
