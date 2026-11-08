#!/usr/bin/env bash
# Run from any working directory. Python dependencies belong in an isolated env.
set -euo pipefail
PLUME_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PLUME_ROOT"
: "${PLUME_EVAL_PYTHON:?Set PLUME_EVAL_PYTHON to an isolated Python with scripts/eval/requirements.txt installed}"
go test ./...
"$PLUME_EVAL_PYTHON" -m pytest -q scripts/eval/test_*.py scripts/tests/test_desktop_update.py internal/transcription/adapters/py/nvidia/tests/test_recluster_speakers.py
cd web/frontend
npm ci
npm run build
npx --no-install playwright test --workers=2
