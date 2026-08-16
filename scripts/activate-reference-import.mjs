#!/usr/bin/env node

import fs from "node:fs";
import { spawn, execFileSync } from "node:child_process";

const args = parseArgs(process.argv.slice(2));
const dbPath = args.db || "/Users/leona/Library/Application Support/plume-desktop/data/quill.db";
const stagedApp = requiredArg("stagedApp");
const installedApp = args.installedApp || "/Applications/Plume.app";
const backupApp = args.backupApp || "/Applications/Plume-before-reference-20260816.app";
const importScript = args.importScript || new URL("./import-voice-memo-archive.mjs", import.meta.url).pathname;
const syncReport = requiredArg("syncReport");
const qualityPlist = args.qualityPlist || "/tmp/com.plume.quality-reruns-20260816.plist";
let apiBase = args.apiBase || "";
const minimumFreeGB = Number(args.minimumFreeGb || 12);

log("controller_started", { staged_app: stagedApp, sync_report: syncReport });
while (activeJobCount() > 0) {
  assertDiskSpace();
  log("waiting_for_existing_jobs", { active_jobs: activeJobCount() });
  await sleep(30_000);
}

assertDiskSpace();
await stopPlume();
installStagedApp();
startPlume();
await waitForBackend();
requireReferenceBackend();

runImport("--start");
resumeQualityController();
runImport("--monitor");
log("controller_completed", {});

function activeJobCount() {
  return Number(sqliteRows("select count(*) n from transcription_jobs where status in ('pending','processing');")[0]?.n || 0);
}

function assertDiskSpace() {
  const output = execFileSync("df", ["-Pk", "/"], { encoding: "utf8" }).trim().split(/\r?\n/).at(-1);
  const availableKB = Number(output.trim().split(/\s+/)[3]);
  const freeGB = availableKB / 1024 / 1024;
  if (!Number.isFinite(freeGB) || freeGB < minimumFreeGB) {
    throw new Error(`Free disk space ${freeGB.toFixed(1)} GB is below ${minimumFreeGB} GB`);
  }
}

async function stopPlume() {
  try {
    execFileSync("osascript", ["-e", 'tell application id "app.plume.desktop" to quit'], { timeout: 30_000 });
  } catch (error) {
    log("quit_request_warning", { error: String(error) });
  }
  for (let attempt = 0; attempt < 60; attempt += 1) {
    if (!plumeBackendPIDs().length) return;
    await sleep(1_000);
  }
  throw new Error("Plume backend did not stop after the existing queue became idle");
}

function installStagedApp() {
  if (!fs.existsSync(stagedApp)) throw new Error(`Staged app is missing: ${stagedApp}`);
  const stagedHash = backendHash(stagedApp);
  if (fs.existsSync(installedApp) && backendHash(installedApp) === stagedHash) {
    log("app_already_installed", { backend_sha256: stagedHash });
    return;
  }
  if (fs.existsSync(installedApp)) {
    if (fs.existsSync(backupApp)) throw new Error(`Backup app path already exists: ${backupApp}`);
    fs.renameSync(installedApp, backupApp);
  }
  execFileSync("ditto", [stagedApp, installedApp], { timeout: 10 * 60_000 });
  execFileSync("codesign", ["--verify", "--deep", "--strict", installedApp], { timeout: 120_000 });
  log("app_installed", { backend_sha256: stagedHash, backup_app: backupApp });
}

function startPlume() {
  const executable = `${installedApp}/Contents/MacOS/Plume`;
  const child = spawn(executable, [], {
    detached: true,
    stdio: "ignore",
    env: { ...process.env, PLUME_AUDIO_STORAGE_MODE: "reference" },
  });
  child.unref();
  log("app_started", { pid: child.pid });
}

async function waitForBackend() {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    try {
      if (!apiBase) apiBase = discoverAPIBase();
      execFileSync("curl", ["-sS", "--fail", `${new URL(apiBase).origin}/health`], { stdio: "ignore", timeout: 5_000 });
      return;
    } catch {
      if (!args.apiBase) apiBase = "";
      await sleep(1_000);
    }
  }
  throw new Error("Updated Plume backend did not become ready");
}

function discoverAPIBase() {
  const pids = plumeBackendPIDs();
  if (!pids.length) throw new Error("Plume backend is not running yet");
  const command = execFileSync("ps", ["eww", "-p", pids[0], "-o", "command="], { encoding: "utf8" });
  const port = command.match(/(?:^|\s)PORT=(\d+)(?:\s|$)/)?.[1];
  if (!port) throw new Error("Could not discover Plume backend port");
  return `http://127.0.0.1:${port}/api/v1`;
}

function requireReferenceBackend() {
  const pids = plumeBackendPIDs();
  if (!pids.length) throw new Error("Plume backend is not running");
  const command = execFileSync("ps", ["eww", "-p", pids[0], "-o", "command="], { encoding: "utf8" });
  if (!/(?:PLUME_)?AUDIO_STORAGE_MODE=reference/.test(command)) {
    throw new Error("Updated backend did not start in reference mode");
  }
  log("reference_mode_verified", { backend_pid: pids[0] });
}

function runImport(mode) {
  execFileSync(process.execPath, [
    importScript,
    "--sync-report", syncReport,
    "--db", dbPath,
    "--api-base", apiBase,
    mode,
  ], { stdio: "inherit", timeout: mode === "--monitor" ? 7 * 24 * 60 * 60_000 : 10 * 60_000 });
}

function resumeQualityController() {
  if (!fs.existsSync(qualityPlist)) {
    log("quality_controller_not_resumed", { reason: "plist_missing", plist: qualityPlist });
    return;
  }
  try {
    execFileSync("launchctl", ["bootstrap", `gui/${process.getuid()}`, qualityPlist], { timeout: 30_000 });
    log("quality_controller_resumed", { plist: qualityPlist });
  } catch (error) {
    const message = String(error?.stderr || error?.message || error);
    if (!/already loaded|service already loaded/i.test(message)) throw error;
  }
}

function plumeBackendPIDs() {
  try {
    return execFileSync("pgrep", ["-f", `${installedApp}/Contents/Resources/backend/quill`], { encoding: "utf8" })
      .trim().split(/\s+/).filter(Boolean);
  } catch {
    return [];
  }
}

function backendHash(appPath) {
  return execFileSync("shasum", ["-a", "256", `${appPath}/Contents/Resources/backend/quill`], { encoding: "utf8" }).trim().split(/\s+/)[0];
}

function sqliteRows(sql) {
  const output = execFileSync("sqlite3", ["-json", dbPath, sql], { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
  return output.trim() ? JSON.parse(output) : [];
}

function log(event, details) {
  console.log(JSON.stringify({ at: new Date().toISOString(), event, ...details }));
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
