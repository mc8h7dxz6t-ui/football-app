#!/usr/bin/env bash
# One-command hands-off stack: Redis + API + auto worker + Streamlit UI
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    echo "Created .env from .env.example — set API_SPORTS_KEY or FVE_UPSTREAM_MODE=hibs then re-run."
    exit 1
  fi
  echo "Missing .env — copy .env.example"
  exit 1
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

if [[ -f .env.paused ]]; then
  # shellcheck disable=SC1091
  set -a
  source .env.paused
  set +a
fi

if [[ "${FVE_PAUSED:-0}" == "1" ]] || [[ "${FVE_PAUSED:-}" =~ ^(true|yes|on)$ ]]; then
  echo "FVE is PAUSED (hibs-bet owns live APIs). See docs/PAUSED.md"
  echo "To resume: set FVE_PAUSED=0 — paths in docs/PAUSED.md and docs/SEPARATE_FEEDS.md"
  exit 0
fi

needs_api_key=1
if [[ "${FVE_UPSTREAM_MODE:-}" =~ ^(hibs|hibs-bet|upstream)$ ]] || [[ -n "${HIBS_UPSTREAM_BASE_URL:-}" ]]; then
  needs_api_key=0
fi
if [[ "${FVE_FEED_MODE:-}" == "separate" ]] && [[ -n "${WATCHLIST_FIXTURES:-}" ]]; then
  needs_api_key=0
fi
if [[ "${FVE_AUTO_WATCHLIST:-1}" =~ ^(0|false|no)$ ]] && [[ -n "${WATCHLIST_FIXTURES:-}" ]]; then
  needs_api_key=0
fi

if [[ "$needs_api_key" == "1" ]] && [[ -z "${API_SPORTS_KEY:-}" && -z "${API_FOOTBALL_KEY:-}" ]]; then
  echo "Set API_SPORTS_KEY in .env for auto watchlist, or:"
  echo "  FVE_UPSTREAM_MODE=hibs + HIBS_UPSTREAM_BASE_URL=..."
  echo "  FVE_FEED_MODE=separate + WATCHLIST_FIXTURES=..."
  exit 1
fi

echo "Starting Football Value Engine stack (docker compose)..."
docker compose --env-file .env --profile ingest --profile ui up -d --build

echo ""
echo "Waiting for API health..."
for _ in $(seq 1 30); do
  if curl -sf "http://localhost:${FVE_API_PORT:-8000}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

curl -sf "http://localhost:${FVE_API_PORT:-8000}/health" | python3 -m json.tool 2>/dev/null || true
bash scripts/preflight_fve.sh || true

echo ""
echo "Stack up:"
echo "  UI:   http://localhost:8501  (enable Inst++ in sidebar)"
echo "  API:  http://localhost:${FVE_API_PORT:-8000}/docs"
echo "  Logs: docker compose logs -f worker api"
echo "  Stop: docker compose down"
