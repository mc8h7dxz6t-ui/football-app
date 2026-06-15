#!/usr/bin/env bash
# VPS: unpause FVE in scrape-heavy mode (zero paid APIs on FVE worker).
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/scripts/vps_unpause_fve_scrape_stack.sh | sudo bash
#
# Prereqs: hibs-bet deployed with /api/fve/lines; FVE at FVE_DEPLOY_PATH (default /opt/fve).
set -euo pipefail

FVE_ROOT="${FVE_DEPLOY_PATH:-/opt/fve}"
HIBS_URL="${HIBS_UPSTREAM_BASE_URL:-https://hibs-bet.co.uk}"
SCRAPE_DIR="${FVE_SCRAPE_LINES_DIR:-/var/lib/fve/scrape-lines}"
BRANCH="${HIBS_FVE_RAW_BRANCH:-main}"
RAW="https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/${BRANCH}"

log() { echo "[fve-scrape] $*"; }

log "1/6 — install hibs-bet FVE lines proxy"
curl -fsSL "${RAW}/scripts/vps_install_hibs_fve_lines.sh" | sudo HIBS_FVE_LINES_RAW="${RAW}/patches/hibs-bet/files" bash

log "2/6 — install Inst++ CLV + market contract on hibs-bet"
curl -fsSL "${RAW}/scripts/vps_install_hibs_inst_clv_contract.sh" | sudo HIBS_INST_RAW="${RAW}/patches/hibs-bet/files" bash || log "WARN inst CLV install skipped"

log "3/6 — scrape lines directory"
mkdir -p "${SCRAPE_DIR}"
chown -R "${SUDO_USER:-root}:${SUDO_USER:-root}" "${SCRAPE_DIR}" 2>/dev/null || true

log "4/6 — FVE .env scrape-heavy block"
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
upsert FVE_FEED_MODE scrape
upsert FVE_SCRAPE_HEAVY 1
upsert FVE_SCRAPE_LINES_DIR "${SCRAPE_DIR}"
upsert HIBS_UPSTREAM_BASE_URL "${HIBS_URL}"
upsert FVE_AUTO_WATCHLIST 1
upsert FVE_WS_DELTA_UPDATES 1
upsert FVE_WS_CLIENT_DELTA 0
upsert WS_MAX_PENDING_SENDS 8
upsert FEED_POLL_SEC_MATCHBOOK 0.5
log "updated ${ENV_FILE}"

log "4/5 — cron: hibs lines collector every 5 min"
CRON_LINE="*/5 * * * * cd ${FVE_ROOT} && HIBS_UPSTREAM_BASE_URL=${HIBS_URL} FVE_SCRAPE_LINES_DIR=${SCRAPE_DIR} /usr/bin/python3 scripts/fve_hibs_lines_collector.py --from-watchlist >> /var/log/fve/lines-collector.log 2>&1"
mkdir -p /var/log/fve
touch /var/log/fve/lines-collector.log
( crontab -l 2>/dev/null | grep -v 'fve_hibs_lines_collector' || true; echo "${CRON_LINE}" ) | crontab -
log "crontab updated"

log "5/5 — initial collect + restart FVE"
if [[ -f "${FVE_ROOT}/scripts/fve_hibs_lines_collector.py" ]]; then
  (cd "${FVE_ROOT}" && HIBS_UPSTREAM_BASE_URL="${HIBS_URL}" FVE_SCRAPE_LINES_DIR="${SCRAPE_DIR}" \
    python3 scripts/fve_hibs_lines_collector.py --from-watchlist) || true
elif [[ -f "${FVE_ROOT}/../football-app/scripts/fve_hibs_lines_collector.py" ]]; then
  (cd "${FVE_ROOT}/../football-app" && HIBS_UPSTREAM_BASE_URL="${HIBS_URL}" FVE_SCRAPE_LINES_DIR="${SCRAPE_DIR}" \
    python3 scripts/fve_hibs_lines_collector.py --from-watchlist) || true
fi

if [[ -f "${FVE_ROOT}/docker-compose.yml" ]]; then
  (cd "${FVE_ROOT}" && docker compose up -d --build api worker redis)
elif systemctl is-active fve-api >/dev/null 2>&1; then
  systemctl restart fve-api fve-worker 2>/dev/null || systemctl restart fve-api
else
  log "INFO no docker-compose or fve-api unit — run: bash scripts/run_scrape_stack.sh"
fi

log "done — verify:"
echo "  curl -sS ${HIBS_URL}/api/health | python3 -c \"import sys,json; d=json.load(sys.stdin); print(d.get('audit_ops',{}).get('clv_beat_close_28d'))\""
echo "  curl -sS \${FVE_API_URL:-http://127.0.0.1:8000}/health | python3 -m json.tool"
echo "  ls -la ${SCRAPE_DIR} | head"
