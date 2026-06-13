#!/usr/bin/env bash
# Hide legacy Football|Racing bar under the unified 3-pill product switcher on hibs-racing.
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/cursor/hide-racing-duplicate-toggle-c4a1/scripts/vps_hide_racing_duplicate_toggle.sh | sudo bash
set -euo pipefail

APP="${HIBS_BET_DEPLOY_PATH:-/opt/hibs-bet}"
RACING="${HIBS_RACING_DEPLOY_PATH:-/opt/hibs-racing}"
RAW="${HIBS_BET_RAW:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/cursor/hide-racing-duplicate-toggle-c4a1}"

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "ERROR: run as root" >&2; exit 1; }

mkdir -p "${APP}/deploy"
curl -fsSL "${RAW}/patches/hibs-bet/files/deploy/product_switcher_inject.py" \
  -o "${APP}/deploy/product_switcher_inject.py"
chmod 755 "${APP}/deploy/product_switcher_inject.py"

echo "==> restart hibs-racing"
systemctl restart hibs-racing
sleep 3

html="$(curl -sS --max-time 45 "http://127.0.0.1:5003/cards" 2>/dev/null || true)"
if echo "${html}" | grep -q 'body:has(#hibs-product-bar-inject) .hibs-product-bar'; then
  echo "OK: duplicate-toggle hide CSS deployed"
else
  echo "WARN: hide CSS not found in /cards HTML — check production guards" >&2
  exit 1
fi

echo "Done — hard-refresh https://hibs-bet.co.uk/racing/cards"
