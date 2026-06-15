#!/usr/bin/env bash
# VPS: install Inst++ CLV value slice + market contract modules on hibs-bet.
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/scripts/vps_install_hibs_inst_clv_contract.sh | sudo bash
set -euo pipefail

APP="${DEPLOY_PATH:-/opt/hibs-bet}"
BRANCH="${HIBS_INST_RAW_BRANCH:-main}"
RAW="${HIBS_INST_RAW:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/${BRANCH}/patches/hibs-bet/files}"

log() { echo "[hibs-inst-clv] $*"; }

[[ -d "${APP}/src/hibs_predictor" ]] || { echo "ERROR: ${APP} not found" >&2; exit 1; }

install_file() {
  local rel="$1"
  local dest="${APP}/${rel}"
  mkdir -p "$(dirname "${dest}")"
  curl -fsSL "${RAW}/${rel}" -o "${dest}"
  log "installed ${rel}"
}

for rel in \
  src/hibs_predictor/clv_institutional.py \
  src/hibs_predictor/market_contract.py \
  src/hibs_predictor/forward_evidence.py \
  src/hibs_predictor/prediction_log.py \
  src/hibs_predictor/price_truth.py \
  src/hibs_predictor/institutional_readiness.py; do
  install_file "${rel}"
done

log "restart hibs-bet"
if systemctl is-active hibs-bet >/dev/null 2>&1; then
  systemctl restart hibs-bet
fi

log "verify institutional-check"
cd "${APP}"
PYTHONPATH=src python3 -m hibs_predictor.main institutional-check 2>&1 | tail -20 || true

log "done"
