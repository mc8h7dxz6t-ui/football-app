#!/usr/bin/env bash
# Stop FVE live ingest/UI and enable pause flag (hibs-bet keeps running).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

docker compose down 2>/dev/null || true
pkill -f "worker.py" 2>/dev/null || true
pkill -f "uvicorn api.main:app" 2>/dev/null || true
pkill -f "streamlit run app.py" 2>/dev/null || true

if [[ -f .env ]]; then
  if grep -q '^FVE_PAUSED=' .env; then
    sed -i 's/^FVE_PAUSED=.*/FVE_PAUSED=1/' .env
  else
    echo "FVE_PAUSED=1" >> .env
  fi
else
  cp .env.paused .env 2>/dev/null || echo "FVE_PAUSED=1" > .env
fi

echo "FVE paused. Live APIs: use hibs-bet (~/Applications)."
echo "See docs/PAUSED.md"
