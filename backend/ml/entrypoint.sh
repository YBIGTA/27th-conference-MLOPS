#!/usr/bin/env bash
set -euo pipefail

INTERVAL=${ML_INTERVAL_SECONDS:-3600}
echo "[ml-runner] start loop, interval=${INTERVAL}s"

while true; do
  ts=$(date -Iseconds)
  echo "[ml-runner] ${ts} running ml/run_hourly.py"
  python /app/ml/run_hourly.py || echo "[ml-runner] job failed (non-zero), will retry next cycle"
  sleep "${INTERVAL}"
done
