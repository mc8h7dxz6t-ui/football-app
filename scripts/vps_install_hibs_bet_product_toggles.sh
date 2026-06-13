#!/usr/bin/env bash
# VPS: apply hibs-bet product toggle patch (racing + trading live dots).
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/scripts/vps_install_hibs_bet_product_toggles.sh | sudo bash
set -euo pipefail

APP="${DEPLOY_PATH:-/opt/hibs-bet}"
PATCH_CORE="${HIBS_PRODUCT_PATCH_URL:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/patches/hibs-bet/product-toggle-core.patch}"
PATCH_FULL="${HIBS_PRODUCT_PATCH_FULL_URL:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/patches/hibs-bet/product-toggle-racing-trading.patch}"

log() { echo "[product-toggle] $*"; }

[[ -d "${APP}/src/hibs_predictor" ]] || { echo "ERROR: hibs-bet not found at ${APP}" >&2; exit 1; }
cd "${APP}"

patch_applied() {
  [[ -f "${APP}/src/hibs_predictor/product_links.py" ]] && \
    grep -q 'augment_stack_ops' "${APP}/src/hibs_predictor/stack_ops_probe.py" 2>/dev/null && \
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
  log "fetching core patch (skips .env.example)"
  if ! curl -fsSL "${PATCH_CORE}" -o "${tmp}" 2>/dev/null; then
    log "core patch missing — trying full patch"
    curl -fsSL "${PATCH_FULL}" -o "${tmp}"
  fi
  apply_patch_file "${tmp}"
fi

if ! patch_applied; then
  echo "ERROR: product toggle code missing — check src/hibs_predictor/product_links.py" >&2
  exit 1
fi
log "code OK"

touch "${APP}/.env"
for kv in HIBS_PRODUCTION=1 HIBS_HEALTH_STACK_PROBE=1; do
  k="${kv%%=*}"
  grep -q "^${k}=" "${APP}/.env" 2>/dev/null || echo "${kv}" >>"${APP}/.env"
done
chown www-data:www-data "${APP}/.env" 2>/dev/null || true

if [[ -x "${APP}/scripts/wire_product_toggles.sh" ]]; then
  bash "${APP}/scripts/wire_product_toggles.sh"
else
  log "wire script missing — run: sudo bash deploy/apply-vps-site-cross-links.sh"
fi

log "done — refresh hibs-bet.co.uk; green dots = racing/trading live"
