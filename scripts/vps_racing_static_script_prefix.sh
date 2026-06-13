#!/usr/bin/env bash
# VPS: fix racecard UI loading wrong /static/racing_ui.js (football stub).
# Rewrites script/stylesheet URLs to /racing/static/* so meeting picker + Smartfolio work.
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/cursor/racing-static-script-prefix-c4a1/scripts/vps_racing_static_script_prefix.sh | sudo bash
set -euo pipefail

BET="${HIBS_BET_DEPLOY_PATH:-/opt/hibs-bet}"
RAW="${HIBS_RACING_STATIC_FIX_RAW:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/cursor/racing-static-script-prefix-c4a1/patches/hibs-bet/files}"

log() { echo "[racing-static-fix] $*"; }

[[ -d "${BET}/deploy" ]] || { echo "ERROR: ${BET}/deploy not found" >&2; exit 1; }

fetch() {
  local rel="$1"
  local dest="${BET}/${rel}"
  mkdir -p "$(dirname "${dest}")"
  curl -fsSL "${RAW}/${rel}" -o "${dest}"
  log "updated ${rel}"
}

for rel in deploy/racing_production_guards.py deploy/racing_nav_prefix_fix.js; do
  fetch "${rel}"
done

log "restarting hibs-racing"
systemctl daemon-reload 2>/dev/null || true
systemctl restart hibs-racing

log "waiting for hibs-racing ping"
for _ in $(seq 1 15); do
  if curl -fsS --max-time 3 http://127.0.0.1:5003/api/ping 2>/dev/null | grep -q '"ok"'; then
    break
  fi
  sleep 2
done

CARDS_HTML="$(curl -fsS --max-time 10 http://127.0.0.1:5003/cards 2>/dev/null || true)"
if echo "${CARDS_HTML}" | grep -q 'src="/racing/static/racing_ui.js"'; then
  log "OK: cards HTML serves /racing/static/racing_ui.js"
elif echo "${CARDS_HTML}" | grep -q 'src="/static/racing_ui.js"'; then
  echo "WARN: cards still references /static/racing_ui.js — check HIBS_URL_PREFIX / production guards" >&2
  exit 1
else
  log "cards HTML fetched (racing_ui.js tag not found — page layout may differ)"
fi

JS_BYTES="$(curl -fsS --max-time 10 -o /dev/null -w '%{size_download}' http://127.0.0.1:5003/static/racing_ui.js 2>/dev/null || echo 0)"
if [[ "${JS_BYTES}" -gt 10000 ]]; then
  log "OK: racing static bundle is ${JS_BYTES} bytes"
else
  echo "WARN: racing_ui.js is only ${JS_BYTES} bytes — expected full racecard UI (~23KB)" >&2
fi

echo ""
echo "Hard-refresh https://hibs-bet.co.uk/racing/cards — meeting picker and Smartfolio should load."
