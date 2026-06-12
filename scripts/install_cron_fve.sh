#!/usr/bin/env bash
# Install daily FVE preflight (local or VPS with stack on localhost:8000)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MARK="# fve-preflight"
CRON_LINE="15 6 * * * cd ${ROOT} && FVE_API_URL=http://127.0.0.1:8000 bash scripts/preflight_fve.sh >> ${ROOT}/logs/fve-preflight.log 2>&1"
mkdir -p "${ROOT}/logs"
( crontab -l 2>/dev/null | grep -v "$MARK" || true
  echo "$CRON_LINE $MARK"
) | crontab -
echo "Installed daily preflight at 06:15 — log: ${ROOT}/logs/fve-preflight.log"
