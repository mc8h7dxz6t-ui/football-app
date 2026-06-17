#!/usr/bin/env bash
# VPS: install FVE line-trader stack beside hibs-bet (port 8010 — gunicorn keeps :8000).
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/scripts/vps_install_fve_line_trader.sh | sudo bash
set -euo pipefail

FVE_ROOT="${FVE_DEPLOY_PATH:-/opt/fve}"
FVE_PORT="${FVE_API_PORT:-8010}"
HIBS_APP="${HIBS_DEPLOY_PATH:-/opt/hibs-bet}"
BRANCH="${HIBS_FVE_RAW_BRANCH:-main}"
RAW="https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/${BRANCH}"

log() { echo "[fve-install] $*"; }

log "sync FVE tree to ${FVE_ROOT}"
mkdir -p "${FVE_ROOT}"
if [[ -d "${FVE_ROOT}/.git" ]]; then
  git -C "${FVE_ROOT}" pull --ff-only origin main 2>/dev/null || true
elif command -v git >/dev/null 2>&1; then
  git clone --depth 1 "https://github.com/mc8h7dxz6t-ui/football-app.git" "${FVE_ROOT}"
else
  log "ERROR: git required to clone football-app into ${FVE_ROOT}" >&2
  exit 1
fi

if [[ -f "${HIBS_APP}/deploy/apply-vps-fve-line-trader.sh" ]]; then
  DEPLOY_PATH="${HIBS_APP}" FVE_DEPLOY_PATH="${FVE_ROOT}" FVE_API_PORT="${FVE_PORT}" \
    bash "${HIBS_APP}/deploy/apply-vps-fve-line-trader.sh"
else
  log "hibs apply script missing — run scrape unpause from football-app"
  curl -fsSL "${RAW}/scripts/vps_unpause_fve_scrape_stack.sh" | sudo \
    FVE_DEPLOY_PATH="${FVE_ROOT}" FVE_API_PORT="${FVE_PORT}" bash
fi

log "preflight"
if [[ -x "${FVE_ROOT}/scripts/preflight_fve.sh" ]]; then
  FVE_API_URL="http://127.0.0.1:${FVE_PORT}" bash "${FVE_ROOT}/scripts/preflight_fve.sh" || true
fi

log "done"
