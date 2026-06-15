#!/usr/bin/env bash
# Fix empty /racing/cards: install raceform.db, run daily_refresh, verify data.
#
# On VPS:
#   sudo bash /opt/hibs-bet/scripts/vps_racing_fix_cards.sh
#   sudo bash /opt/hibs-bet/scripts/vps_racing_fix_cards.sh /tmp/raceform.db
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib_racing_vps_probe.sh
source "${ROOT}/scripts/lib_racing_vps_probe.sh"

APP="${HIBS_RACING_DEPLOY_PATH:-/opt/hibs-racing}"
BET="${HIBS_BET_DEPLOY_PATH:-/opt/hibs-bet}"
RACEFORM_SRC="${1:-}"

step() { echo ""; echo "==> $*"; }

[[ -d "${APP}" ]] || { echo "ERROR: ${APP} missing" >&2; exit 1; }
mkdir -p "${APP}/data"

step "raceform.db"
dest="${APP}/data/raceform.db"
if [[ -n "${RACEFORM_SRC}" && -f "${RACEFORM_SRC}" ]]; then
  src_real="$(readlink -f "${RACEFORM_SRC}" 2>/dev/null || echo "${RACEFORM_SRC}")"
  dest_real="$(readlink -f "${dest}" 2>/dev/null || echo "${dest}")"
  if [[ "${src_real}" != "${dest_real}" ]]; then
    cp -f "${RACEFORM_SRC}" "${dest}"
    echo "    installed from ${RACEFORM_SRC}"
  else
    echo "    already at ${dest}"
  fi
elif ! racing_vps_resolve_raceform "${APP}" >/dev/null; then
  echo "ERROR: raceform.db missing at ${APP}/data/raceform.db" >&2
  echo "" >&2
  echo "From Mac (new terminal):" >&2
  echo "  ./scripts/upload_raceform_to_vps.sh" >&2
  echo "" >&2
  echo "Or copy to VPS then re-run:" >&2
  echo "  sudo bash ${BET}/scripts/vps_racing_fix_cards.sh /tmp/raceform.db" >&2
  exit 1
fi
racing_vps_ensure_raceform_env "${APP}"

step "data dir"
ls -lh "${APP}/data/"*.sqlite "${APP}/data/"*.db 2>/dev/null || echo "    (no sqlite yet — refresh will create)"

step "build cards (ingest-raceform + fetch-cards)"
cd "${APP}"
REFRESH_LOG="${APP}/logs/vps_daily_refresh.log"
mkdir -p "${APP}/logs"
chown www-data:www-data "${APP}/logs" 2>/dev/null || true
YEAR="$(date +%Y)"
RF="$(racing_vps_resolve_raceform "${APP}")"
HIBS_CLI="${APP}/.venv/bin/hibs-racing"
refresh_rc=0

if [[ -x "${HIBS_CLI}" && -f "${RF}" ]]; then
  echo "    using hibs-racing CLI (same as Mac manual launch)"
  set +e
  {
    echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) ingest+fetch ====="
    sudo -u www-data env HOME="${APP}" PYTHONPATH=src "${HIBS_CLI}" \
      ingest-raceform "${RF}" --year "${YEAR}" --pipeline
    for day in 0 1; do
      echo "fetch-cards --day ${day}"
      sudo -u www-data env HOME="${APP}" PYTHONPATH=src "${HIBS_CLI}" \
        fetch-cards --source racing_api --day "${day}" --score
    done
  } 2>&1 | tee "${REFRESH_LOG}" | tail -60
  refresh_rc=${PIPESTATUS[0]}
  set -e
elif [[ -f scripts/daily_refresh.sh ]]; then
  echo "    fallback: scripts/daily_refresh.sh"
  set +e
  sudo -u www-data env HOME="${APP}" bash scripts/daily_refresh.sh 2>&1 | tee "${REFRESH_LOG}" | tail -50
  refresh_rc=${PIPESTATUS[0]}
  set -e
else
  echo "ERROR: no hibs-racing CLI and no daily_refresh.sh" >&2
  exit 1
fi

echo "    full log: ${REFRESH_LOG}"
if [[ "${refresh_rc}" -ne 0 ]]; then
  echo "WARN: card build exit ${refresh_rc} — need RACING_API key in ${APP}/.env?" >&2
  grep -iE 'error|fail|traceback|api' "${REFRESH_LOG}" 2>/dev/null | tail -15 >&2 || true
  echo "Mac fallback: ./scripts/mac_racing_cards_publish.sh" >&2
fi

step "backfill last ${RACING_FORM_RUNS:-6} runs form"
if [[ -f "${ROOT}/scripts/racing_backfill_ranker_form_sqlite.py" && -f "${APP}/data/feature_store.sqlite" ]]; then
  python3 "${ROOT}/scripts/racing_backfill_ranker_form_sqlite.py" \
    "${APP}/data/feature_store.sqlite" --raceform "${RF}" --runs "${RACING_FORM_RUNS:-6}" || {
    echo "WARN: form backfill failed" >&2
  }
else
  echo "    skip — missing backfill script or feature_store.sqlite"
fi

step "restart racing"
racing_vps_restart_service
racing_vps_wait_ping 45 3 || {
  journalctl -u hibs-racing -n 20 --no-pager
  exit 1
}

step "cards check (local, up to 120s)"
cards_code="$(racing_vps_http_code "http://127.0.0.1:5003/cards" 120)"
echo "    /cards -> ${cards_code}"

if [[ -x "${APP}/.venv/bin/python" ]]; then
  sudo -u www-data env HOME="${APP}" PYTHONPATH=src "${APP}/.venv/bin/python" -c "
import os, sqlite3
from pathlib import Path
data = Path('${APP}/data')
for name in ('feature_store.sqlite', 'hibs_racing.db'):
    p = data / name
    if not p.is_file():
        continue
    con = sqlite3.connect(p)
    for tbl in ('cards', 'card', 'meetings', 'races'):
        try:
            n = con.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
            if n:
                print(f'{name}.{tbl}: {n} rows')
        except Exception:
            pass
    con.close()
" 2>/dev/null || true
fi

html="$(curl -sS --max-time 120 http://127.0.0.1:5003/cards 2>/dev/null | head -c 8000 || true)"
if echo "${html}" | grep -qi 'no card in db'; then
  echo "WARN: UI still shows 'No card in DB' — Mac fallback:" >&2
  echo "  cd ~/hibs-racing && bash scripts/daily_refresh.sh" >&2
  echo "  cd ~/hibs-betting-app && ./scripts/deploy_racing_data_to_vps.sh" >&2
  exit 1
fi

if [[ "${cards_code}" =~ ^(200|302)$ ]]; then
  echo ""
  echo "Cards fix GREEN — https://hibs-bet.co.uk/racing/cards"
  exit 0
fi

echo "ERROR: cards not healthy (code=${cards_code})" >&2
exit 1
