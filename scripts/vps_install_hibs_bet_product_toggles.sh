#!/usr/bin/env bash
# VPS: wire product toggle navigation (/, /racing/cards, /harvested-execution).
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/scripts/vps_install_hibs_bet_product_toggles.sh | sudo bash
set -euo pipefail

APP="${DEPLOY_PATH:-/opt/hibs-bet}"
PATCH_CORE="${HIBS_PRODUCT_PATCH_URL:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/patches/hibs-bet/product-toggle-core.patch}"

log() { echo "[product-toggle] $*"; }

[[ -d "${APP}/src/hibs_predictor" ]] || { echo "ERROR: hibs-bet not found at ${APP}" >&2; exit 1; }
cd "${APP}"

patch_applied() {
  [[ -f "${APP}/deploy/product_switcher_inject.py" ]] && \
    grep -q 'racing_cards_url' "${APP}/src/hibs_predictor/product_links.py" 2>/dev/null && \
    grep -q 'HibsProductStacks' "${APP}/static/hibs_product_stacks.js" 2>/dev/null
}

apply_patch_file() {
  local patch_file="$1"
  log "applying ${patch_file}"
  if git -C "${APP}" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "${APP}" apply --3way "${patch_file}" 2>/dev/null && return 0
  fi
  patch -p1 --forward <"${patch_file}" || true
  find "${APP}" -name '*.rej' -delete 2>/dev/null || true
}

if ! patch_applied; then
  tmp="$(mktemp)"
  trap 'rm -f "${tmp}"' EXIT
  log "fetching patch"
  curl -fsSL "${PATCH_CORE}" -o "${tmp}"
  apply_patch_file "${tmp}"
fi

if ! patch_applied; then
  echo "ERROR: product toggle code missing" >&2
  exit 1
fi
log "code OK"

if [[ -x "${APP}/scripts/wire_product_toggles.sh" ]]; then
  bash "${APP}/scripts/wire_product_toggles.sh"
else
  touch "${APP}/.env"
  for kv in \
    HIBS_PRODUCTION=1 \
    HIBS_RACING_BASE_URL=/racing \
    HIBS_TRADING_STATUS_URL=/harvested-execution \
    HIBS_PORTFOLIO_API_URL=/api/racing/portfolio/summary; do
    k="${kv%%=*}"
    v="${kv#*=}"
    grep -q "^${k}=" "${APP}/.env" 2>/dev/null && sed -i "s|^${k}=.*|${k}=${v}|" "${APP}/.env" || echo "${kv}" >>"${APP}/.env"
  done
  systemctl restart hibs-bet 2>/dev/null || true
  systemctl restart hibs-racing 2>/dev/null || true
fi

log "done — toggle: Football=/  Racing=/racing/cards  Trading=/harvested-execution"
