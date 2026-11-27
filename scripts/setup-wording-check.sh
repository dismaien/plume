#!/usr/bin/env bash
# Install the independent audio reader into the same model root as WhisperX.
set -euo pipefail
root="${1:-${WHISPERX_ENV:-}}"
if [[ -z "$root" ]]; then
    if [[ "$(uname -s)" != Darwin ]]; then
        echo 'Pass the Plume model environment directory as the first argument.' >&2
        exit 2
    fi
    root="$HOME/Library/Application Support/plume-desktop/data/whisperx-env"
fi
dest="$root/wording-check"
mkdir -p "$dest"
if [[ ! -x "$dest/.venv/bin/python" ]]; then
    uv venv --python 3.12 "$dest/.venv"
fi
uv pip install --python "$dest/.venv/bin/python" 'transformers==5.22.2' 'torch==2.15.0' 'numpy==2.5.4' 'librosa==1.0.0'
"$dest/.venv/bin/python" - "$dest/model" <<'PY'
from huggingface_hub import snapshot_download
import sys
snapshot_download("Qwen/Qwen3-ASR-1.7B-hf",
                  revision="bcd2b5b7f32b480ab5790554cfa8347f246a14f3",
                  allow_patterns=["*.json", "*.jinja", "model.safetensors"],
                  local_dir=sys.argv[1])
PY
