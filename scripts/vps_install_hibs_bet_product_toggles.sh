#!/usr/bin/env bash
# VPS: wire product toggle navigation (/, /racing/cards, /harvested-execution).
#
# Prefer: curl -fsSL .../vps_finish_product_toggles.sh | sudo bash
# This script: patch if needed, else wire only.
set -euo pipefail

APP="${DEPLOY_PATH:-/opt/hibs-bet}"
PATCH_CORE="${HIBS_PRODUCT_PATCH_URL:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/patches/hibs-bet/product-toggle-core.patch}"
FINISH_URL="${HIBS_TOGGLE_FINISH_URL:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/scripts/vps_finish_product_toggles.sh}"

log() { echo "[product-toggle] $*"; }

[[ -d "${APP}/src/hibs_predictor" ]] || { echo "ERROR: hibs-bet not found at ${APP}" >&2; exit 1; }
cd "${APP}"

toggle_ready() {
  [[ -f "${APP}/deploy/product_switcher_inject.py" ]] && \
    grep -q 'racing_cards_url' "${APP}/src/hibs_predictor/product_links.py" 2>/dev/null
}

toggle_partial() {
  [[ -f "${APP}/src/hibs_predictor/product_links.py" ]] && \
    [[ -f "${APP}/src/hibs_predictor/stack_ops_probe.py" ]]
}

wire_env() {
  if [[ -x "${APP}/scripts/wire_product_toggles.sh" ]]; then
    bash "${APP}/scripts/wire_product_toggles.sh"
    return
  fi
  touch "${APP}/.env"
  for kv in \
    HIBS_PRODUCTION=1 \
    HIBS_RACING_BASE_URL=/racing \
    HIBS_TRADING_STATUS_URL=/harvested-execution \
    HIBS_PORTFOLIO_API_URL=/api/racing/portfolio/summary; do
    k="${kv%%=*}"
    v="${kv#*=}"
    if grep -q "^${k}=" "${APP}/.env" 2>/dev/null; then
      sed -i "s|^${k}=.*|${k}=${v}|" "${APP}/.env" 2>/dev/null || true
    else
      echo "${kv}" >>"${APP}/.env"
    fi
  done
  chown www-data:www-data "${APP}/.env" 2>/dev/null || true
  systemctl restart hibs-bet 2>/dev/null || true
  systemctl restart hibs-racing 2>/dev/null || true
}

find "${APP}" -name '*.rej' -delete 2>/dev/null || true

if toggle_ready; then
  log "toggle code already present"
elif toggle_partial; then
  log "partial install detected — fetching finish script (no patch)"
  curl -fsSL "${FINISH_URL}" | bash
  exit $?
else
  tmp="$(mktemp)"
  trap 'rm -f "${tmp}"' EXIT
  log "fetching patch"
  curl -fsSL "${PATCH_CORE}" -o "${tmp}"
  patch -p1 --forward <"${tmp}" || true
  find "${APP}" -name '*.rej' -delete 2>/dev/null || true
  if ! toggle_ready; then
    log "patch incomplete — running finish script"
    curl -fsSL "${FINISH_URL}" | bash
    exit $?
  fi
fi

wire_env
log "done — toggle: Football=/  Racing=/racing/cards  Trading=/harvested-execution"
