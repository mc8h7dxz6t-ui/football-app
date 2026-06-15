#!/usr/bin/env bash
# VPS: deploy trading REST backup (HTTP tape when WSS shared/blocked).
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/scripts/vps_apply_trading_rest_fallback.sh | sudo bash
#   curl -fsSL .../vps_apply_trading_rest_fallback.sh | sudo bash -s -- --prefer-rest
set -euo pipefail

APP="${DEPLOY_PATH:-/opt/hibs-bet}"
TRADING="${TRADING_INSTALL_ROOT:-/opt/trading-core}"
RAW="${HIBS_TRADING_REST_RAW:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/patches/hibs-bet/files/trading}"
PREFER=0
[[ "${1:-}" == "--prefer-rest" ]] && PREFER=1

log() { echo "[trading-rest] $*"; }

fetch() {
  local rel="$1"
  local dest="${APP}/${rel}"
  mkdir -p "$(dirname "${dest}")"
  curl -fsSL "${RAW}/${rel}" -o "${dest}"
  log "updated ${rel}"
}

for rel in \
  src/hibs_predictor/trading_core/rest_market_fallback.py \
  src/hibs_predictor/trading_core/metrics.py \
  src/hibs_predictor/trading_core/orchestrator.py \
  deploy/apply-trading-rest-fallback.sh; do
  fetch "${rel}"
done

chmod +x "${APP}/deploy/apply-trading-rest-fallback.sh"

# trading-core may share same src tree on some installs
if [[ -d "${TRADING}/src/hibs_predictor/trading_core" ]]; then
  for f in rest_market_fallback.py metrics.py orchestrator.py; do
    cp "${APP}/src/hibs_predictor/trading_core/${f}" "${TRADING}/src/hibs_predictor/trading_core/${f}"
    log "synced trading-core ${f}"
  done
fi

ARGS=()
[[ "${PREFER}" -eq 1 ]] && ARGS+=(--prefer-rest)
bash "${APP}/deploy/apply-trading-rest-fallback.sh" "${ARGS[@]}"

log "done — curl -s http://127.0.0.1:9108/ready"
