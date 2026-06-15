#!/usr/bin/env bash
# VPS: ensure hibs-bet venv exists and requirements are installed (gunicorn needs python-dotenv).
#
#   curl -fsSL .../scripts/vps_ensure_hibs_bet_venv.sh | sudo bash
set -euo pipefail

APP="${DEPLOY_PATH:-/opt/hibs-bet}"

log() { echo "[hibs-venv] $*"; }

[[ -d "${APP}/src/hibs_predictor" ]] || { echo "ERROR: ${APP} not found" >&2; exit 1; }
[[ -f "${APP}/requirements.txt" ]] || { echo "ERROR: ${APP}/requirements.txt missing" >&2; exit 1; }

if [[ ! -x "${APP}/.venv/bin/pip" ]]; then
  log "creating ${APP}/.venv"
  python3 -m venv "${APP}/.venv"
fi

log "pip install -r requirements.txt"
"${APP}/.venv/bin/pip" install -q -r "${APP}/requirements.txt"

if [[ -f "${APP}/deploy/hibs-bet.service" ]]; then
  if ! grep -qF "${APP}/.venv/bin/gunicorn" /etc/systemd/system/hibs-bet.service 2>/dev/null; then
    log "install systemd unit from deploy/hibs-bet.service"
    cp "${APP}/deploy/hibs-bet.service" /etc/systemd/system/hibs-bet.service
    if [[ "${APP}" != "/opt/hibs-bet" ]]; then
      sed -i "s|/opt/hibs-bet|${APP}|g" /etc/systemd/system/hibs-bet.service
    fi
    systemctl daemon-reload
  fi
fi

chown -R www-data:www-data "${APP}/.venv" "${APP}/.cache" 2>/dev/null || true

"${APP}/.venv/bin/python" -c "import dotenv, flask, gunicorn" >/dev/null
log "venv ok — $("${APP}/.venv/bin/python" -c 'import dotenv; print(dotenv.__version__)')"
