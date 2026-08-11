#!/bin/sh

set -eu

exec /usr/bin/caffeinate -dimsu \
  /usr/bin/python3 \
  /Users/leona/Documents/Playground/plume/scripts/run-overnight-quality-reruns.py \
  --hours 24 \
  --min-score 12 \
  --batch-limit 25 \
  --batch-max-hours 60 \
  --min-free-gb 12 \
  >> '/Users/leona/Documents/Voice Notes Archive/20-quill-processing/retranscription-runs/overnight-controller-launch.log' \
  2>&1
