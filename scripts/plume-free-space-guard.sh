#!/bin/bash

set -u

threshold_gib="${1:-12}"
check_interval_seconds="${PLUME_DISK_GUARD_INTERVAL_SECONDS:-60}"
log_path="${PLUME_DISK_GUARD_LOG:-/tmp/plume-free-space-guard.log}"

if ! [[ "$threshold_gib" =~ ^[0-9]+$ ]] || (( threshold_gib < 1 )); then
  echo "Threshold must be a positive integer number of GiB." >&2
  exit 2
fi

threshold_kb=$((threshold_gib * 1024 * 1024))

while true; do
  free_kb="$(df -Pk / | tail -n 1 | tr -s ' ' | cut -d ' ' -f 4)"

  if ! [[ "$free_kb" =~ ^[0-9]+$ ]]; then
    printf '%s unable to read free disk space; leaving Plume running\n' "$(date -Iseconds)" >> "$log_path"
    sleep "$check_interval_seconds"
    continue
  fi

  printf '%s free_kb=%s threshold_kb=%s\n' "$(date -Iseconds)" "$free_kb" "$threshold_kb" >> "$log_path"

  if (( free_kb < threshold_kb )); then
    printf '%s threshold reached; stopping Plume and transcription workers\n' "$(date -Iseconds)" >> "$log_path"
    osascript -e 'tell application "Plume" to quit' >/dev/null 2>&1 || true
    pkill -TERM -f 'python -m whisperx|uv run.*whisperx|/Applications/Plume.app/Contents/Resources/backend/quill' 2>/dev/null || true
    exit 0
  fi

  sleep "$check_interval_seconds"
done
