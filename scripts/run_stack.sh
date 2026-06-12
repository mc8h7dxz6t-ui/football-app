#!/usr/bin/env bash
# One-command hands-off stack: Redis + API + auto worker + Streamlit UI
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    echo "Created .env from .env.example — set API_SPORTS_KEY then re-run."
    exit 1
  fi
  echo "Missing .env — copy .env.example and set API_SPORTS_KEY"
  exit 1
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

if [[ -z "${API_SPORTS_KEY:-}" && -z "${API_FOOTBALL_KEY:-}" ]]; then
  echo "Set API_SPORTS_KEY in .env for auto watchlist"
  exit 1
fi

echo "Starting Football Value Engine stack (docker compose)..."
docker compose --env-file .env up -d --build

echo ""
echo "Waiting for API health..."
for _ in $(seq 1 30); do
  if curl -sf "http://localhost:${FVE_API_PORT:-8000}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

curl -sf "http://localhost:${FVE_API_PORT:-8000}/health" | python3 -m json.tool 2>/dev/null || true

echo ""
echo "Stack up:"
echo "  UI:   http://localhost:8501  (enable Inst++ in sidebar)"
echo "  API:  http://localhost:${FVE_API_PORT:-8000}/docs"
echo "  Logs: docker compose logs -f worker api"
echo "  Stop: docker compose down"
