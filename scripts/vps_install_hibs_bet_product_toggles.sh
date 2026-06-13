#!/usr/bin/env bash
# VPS: apply hibs-bet product toggle patch (racing + trading live dots).
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/scripts/vps_install_hibs_bet_product_toggles.sh | sudo bash
set -euo pipefail

APP="${DEPLOY_PATH:-/opt/hibs-bet}"
PATCH_URL="${HIBS_PRODUCT_PATCH_URL:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/patches/hibs-bet/product-toggle-racing-trading.patch}"

log() { echo "[product-toggle] $*"; }

[[ -d "${APP}/src/hibs_predictor" ]] || { echo "ERROR: hibs-bet not found at ${APP}" >&2; exit 1; }
cd "${APP}"

if grep -q 'stack_ops_probe' "${APP}/src/hibs_predictor/stack_ops_probe.py" 2>/dev/null; then
  log "product toggle patch already applied"
else
  tmp="$(mktemp)"
  trap 'rm -f "${tmp}"' EXIT
  log "fetching patch"
  curl -fsSL "${PATCH_URL}" -o "${tmp}"
  log "applying patch"
  if git -C "${APP}" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "${APP}" apply --3way "${tmp}" 2>/dev/null || patch -p1 --forward <"${tmp}" || true
  else
    patch -p1 --forward <"${tmp}" || true
  fi
  find "${APP}" -name '*.rej' -delete 2>/dev/null || true
  grep -q 'stack_ops_probe' "${APP}/src/hibs_predictor/stack_ops_probe.py" || {
    echo "ERROR: patch apply failed" >&2
    exit 1
  }
fi

if [[ -x "${APP}/scripts/wire_product_toggles.sh" ]]; then
  bash "${APP}/scripts/wire_product_toggles.sh"
else
  log "wire script missing — set cross-links manually"
fi

log "done — refresh hibs-bet.co.uk and check product bar dots"
