#!/usr/bin/env bash
# Zero paid API — scrape-heavy FVE stack (hibs lines collector + FotMob watchlist).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env — review scrape-heavy block, then re-run."
  exit 1
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

export FVE_PAUSED="${FVE_PAUSED:-0}"
export FVE_FEED_MODE="${FVE_FEED_MODE:-scrape}"
export FVE_SCRAPE_HEAVY="${FVE_SCRAPE_HEAVY:-1}"
export FVE_SCRAPE_LINES_DIR="${FVE_SCRAPE_LINES_DIR:-${ROOT}/data/scrape-lines}"
export FVE_AUTO_WATCHLIST="${FVE_AUTO_WATCHLIST:-1}"
mkdir -p "${FVE_SCRAPE_LINES_DIR}"

echo "Collecting lines from hibs (if reachable)..."
python3 scripts/fve_hibs_lines_collector.py --from-watchlist || true

echo "Starting stack..."
bash scripts/run_stack.sh
