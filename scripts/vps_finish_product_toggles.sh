#!/usr/bin/env bash
# Finish product toggle on VPS — no patch, copies files + wires .env.
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/scripts/vps_finish_product_toggles.sh | sudo bash
set -euo pipefail

APP="${DEPLOY_PATH:-/opt/hibs-bet}"
RAW="${HIBS_TOGGLE_FILES_RAW:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/patches/hibs-bet/files}"

log() { echo "[toggle-finish] $*"; }

[[ -d "${APP}/src/hibs_predictor" ]] || { echo "ERROR: ${APP} not found" >&2; exit 1; }
cd "${APP}"

log "cleaning leftover .rej files"
find "${APP}" -name '*.rej' -delete 2>/dev/null || true

fetch() {
  local rel="$1"
  local dest="${APP}/${rel}"
  mkdir -p "$(dirname "${dest}")"
  curl -fsSL "${RAW}/${rel}" -o "${dest}"
  log "updated ${rel}"
}

for rel in \
  src/hibs_predictor/product_links.py \
  templates/_product_switcher.html \
  deploy/product_switcher_inject.py \
  deploy/racing_production_guards.py \
  scripts/wire_product_toggles.sh; do
  fetch "${rel}"
done

chmod +x "${APP}/scripts/wire_product_toggles.sh" 2>/dev/null || true

log "wire .env cross-links"
bash "${APP}/scripts/wire_product_toggles.sh"

log "done"
echo ""
echo "Toggle URLs:"
echo "  Football  -> /"
echo "  Racing    -> /racing/cards"
echo "  Trading   -> /harvested-execution"
echo ""
echo "Hard-refresh https://hibs-bet.co.uk in your browser."
