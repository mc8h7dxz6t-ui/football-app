#!/usr/bin/env bash
# Wire football ↔ racing ↔ trading product toggles on VPS (cross-link .env + restart).
#
#   sudo bash /opt/hibs-bet/scripts/wire_product_toggles.sh
#   DEPLOY_HOST=77.68.89.73 ./scripts/wire_product_toggles.sh --remote
set -euo pipefail

APP="${DEPLOY_PATH:-/opt/hibs-bet}"
REMOTE=0
for arg in "$@"; do
  [[ "${arg}" == "--remote" ]] && REMOTE=1
done

if [[ "${REMOTE}" -eq 1 ]]; then
  HOST="${DEPLOY_HOST:-77.68.89.73}"
  USER="${DEPLOY_USER:-root}"
  exec ssh -o BatchMode=yes -o ConnectTimeout=25 "${USER}@${HOST}" \
    "export DEPLOY_PATH='${APP}'; bash '${APP}/scripts/wire_product_toggles.sh'"
fi

[[ "$(id -u)" -eq 0 ]] || { echo "run as root on VPS" >&2; exit 1; }
cd "${APP}"

log() { echo "[product-toggle] $*"; }

touch "${APP}/.env"
for kv in HIBS_PRODUCTION=1 HIBS_HEALTH_STACK_PROBE=1 HIBS_RACING_BASE_URL=/racing HIBS_TRADING_STATUS_URL=/harvested-execution HIBS_PORTFOLIO_API_URL=/api/racing/portfolio/summary; do
  k="${kv%%=*}"
  v="${kv#*=}"
  if grep -q "^${k}=" "${APP}/.env" 2>/dev/null; then
    sed -i "s|^${k}=.*|${k}=${v}|" "${APP}/.env" 2>/dev/null || true
  else
    echo "${kv}" >>"${APP}/.env"
  fi
done

if [[ -f "${APP}/deploy/apply-vps-site-cross-links.sh" ]]; then
  log "cross-link racing + trading URLs in football .env"
  bash "${APP}/deploy/apply-vps-site-cross-links.sh"
else
  log "WARN: apply-vps-site-cross-links.sh missing — set HIBS_RACING_BASE_URL manually"
fi

if systemctl is-enabled hibs-bet &>/dev/null; then
  log "restart hibs-bet"
  systemctl restart hibs-bet
  sleep 2
fi

PY="${APP}/.venv/bin/python3"
[[ -x "${PY}" ]] || PY=python3
export PYTHONPATH="${APP}/src"
set -a
# shellcheck disable=SC1091
source "${APP}/.env"
set +a

log "stack probe snapshot"
"${PY}" -c "
from hibs_predictor.stack_ops_probe import probe_racing_stack, probe_trading_stack
import json
print(json.dumps({'racing': probe_racing_stack(), 'trading': probe_trading_stack()}, indent=2))
"

if [[ -f "${APP}/scripts/verify_hibs_ui_smoke.sh" ]]; then
  log "UI smoke (football + racing + trading)"
  bash "${APP}/scripts/verify_hibs_ui_smoke.sh" || true
fi

log "done — product bar should show green dots when stacks are live"
