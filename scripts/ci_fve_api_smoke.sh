#!/usr/bin/env bash
# CI smoke: docker compose api + redis, curl /health (no book API keys).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

export FVE_PAUSED=1
export COMPOSE_PROFILES=""

echo "==> docker compose up redis api"
docker compose --env-file .env up -d --build redis api

cleanup() {
  docker compose --env-file .env down -v --remove-orphans 2>/dev/null || true
}
trap cleanup EXIT

echo "==> wait for /health"
for _ in $(seq 1 30); do
  if curl -fsS --max-time 3 http://127.0.0.1:8000/health >/tmp/fve_ci_health.json 2>/dev/null; then
    python3 -m json.tool /tmp/fve_ci_health.json | head -20
    echo "OK: FVE API smoke passed"
    exit 0
  fi
  sleep 2
done

echo "FAIL: API did not become healthy in time" >&2
docker compose logs api --tail 40 || true
exit 1
