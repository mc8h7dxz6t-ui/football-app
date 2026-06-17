#!/usr/bin/env bash
# VPS: unpause FVE with hibs-bet upstream (no duplicate book API quota).
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/scripts/vps_unpause_fve_hibs_upstream.sh | sudo bash
#
# Prereqs: hibs-bet deployed; FVE docker compose or systemd unit on same host.
set -euo pipefail

FVE_ROOT="${FVE_DEPLOY_PATH:-/opt/fve}"
HIBS_URL="${HIBS_UPSTREAM_BASE_URL:-https://hibs-bet.co.uk}"
BRANCH="${HIBS_FVE_RAW_BRANCH:-main}"
RAW="https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/${BRANCH}"

log() { echo "[fve-unpause] $*"; }

log "1/4 — install hibs-bet FVE lines proxy"
curl -fsSL "${RAW}/scripts/vps_install_hibs_fve_lines.sh" | sudo HIBS_FVE_LINES_RAW="${RAW}/patches/hibs-bet/files" bash

log "2/4 — verify hibs lines endpoint"
PROBE_KEY="${FVE_PROBE_FIXTURE:-Arsenal v Chelsea}"
ENC_KEY="${PROBE_KEY// /%20}"
code=$(curl -sS -o /dev/null -w '%{http_code}' "${HIBS_URL}/api/fve/lines/${ENC_KEY}" || echo "000")
if [[ "$code" != "200" && "$code" != "404" ]]; then
  log "WARN hibs /api/fve/lines returned HTTP ${code} (404 ok if fixture not in bundle)"
fi

log "3/4 — write FVE .env upstream block"
mkdir -p "${FVE_ROOT}"
ENV_FILE="${FVE_ROOT}/.env"
touch "${ENV_FILE}"
upsert() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "${ENV_FILE}" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "${ENV_FILE}"
  else
    echo "${key}=${val}" >> "${ENV_FILE}"
  fi
}
upsert FVE_PAUSED 0
upsert FVE_UPSTREAM_MODE hibs
upsert HIBS_UPSTREAM_BASE_URL "${HIBS_URL}"
upsert FVE_WS_DELTA_UPDATES 1
upsert FVE_WS_CLIENT_DELTA 0
upsert FEED_POLL_SEC_MATCHBOOK 0.5
upsert WS_MAX_PENDING_SENDS 8
log "updated ${ENV_FILE}"

log "4/4 — restart FVE stack (if present)"
if [[ -f "${FVE_ROOT}/docker-compose.yml" ]]; then
  (cd "${FVE_ROOT}" && COMPOSE_PROFILES=ingest docker compose up -d --build api worker redis)
elif systemctl is-active fve-api >/dev/null 2>&1; then
  systemctl restart fve-api fve-worker 2>/dev/null || systemctl restart fve-api
else
  log "INFO no docker-compose or fve-api unit — set env manually and start uvicorn + worker"
fi

log "done — check:"
echo "  curl -sS ${HIBS_URL}/api/health | head"
echo "  curl -sS \${FVE_API_URL:-http://127.0.0.1:8000}/health | python3 -m json.tool"
