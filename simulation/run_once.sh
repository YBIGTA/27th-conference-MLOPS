#!/usr/bin/env bash
set -euo pipefail

TS="$(date -u +"%Y-%m-%dT%H:%M:00Z")"

python simulation/prefill.py \
  --min-price-rows 40 \
  --json-path '' \
  --csv-path ''

python simulation/strategy_prefill.py \
  --min-price-rows 40 \
  --csv-path ''
