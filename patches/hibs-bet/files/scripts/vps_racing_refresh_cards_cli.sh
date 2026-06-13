#!/usr/bin/env bash
# Refresh racing cards WITHOUT the web UI (avoids gunicorn worker crash on "Refresh 24h").
#
# On VPS:
#   sudo bash /opt/hibs-bet/scripts/vps_racing_refresh_cards_cli.sh
set -euo pipefail

APP="${HIBS_RACING_DEPLOY_PATH:-/opt/hibs-racing}"
BET="${HIBS_BET_DEPLOY_PATH:-/opt/hibs-bet}"
LOG="${APP}/logs/cli_card_refresh.log"
YEAR="$(date +%Y)"

# shellcheck source=lib_racing_vps_probe.sh
source "${BET}/scripts/lib_racing_vps_probe.sh"
# shellcheck source=lib_racing_api_env.sh
source "${BET}/scripts/lib_racing_api_env.sh"

mkdir -p "${APP}/logs" "${APP}/data"
exec > >(tee -a "${LOG}") 2>&1

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) CLI card refresh ====="
echo "NOTE: UI Refresh is disabled on production (HIBS_DISABLE_UI_REFRESH=1)."

HIBS_CLI="${APP}/.venv/bin/hibs-racing"
ENV_FILE="${APP}/.env"

[[ -x "${HIBS_CLI}" ]] || { echo "ERROR: ${HIBS_CLI} missing — run vps_racing_bootstrap.sh"; exit 1; }
racing_vps_repair_raceform_env "${APP}" || exit 1
RF="$(racing_vps_resolve_raceform "${APP}")"
racing_api_require_credentials "${ENV_FILE}" "VPS ${ENV_FILE}" || {
  echo "Mac: ./scripts/sync_racing_api_env_to_vps.sh" >&2
  exit 1
}

systemctl stop hibs-racing 2>/dev/null || true
pkill -f 'gunicorn.*5003' 2>/dev/null || true
sleep 2

echo "==> ingest-raceform ${RF} --year ${YEAR}"
sudo -u www-data env HOME="${APP}" PYTHONPATH=src RACEFORM_DB_PATH="${RF}" "${HIBS_CLI}" \
  ingest-raceform "${RF}" --year "${YEAR}" --pipeline || {
  echo "WARN: ingest-raceform failed — continuing with API fetch" >&2
}

fetch_rc=0
paid_ok=0
for day in 0 1; do
  echo "==> fetch-cards --day ${day} (Basic plan)"
  if sudo -u www-data env HOME="${APP}" PYTHONPATH=src RACEFORM_DB_PATH="${RF}" "${HIBS_CLI}" \
    fetch-cards --source racing_api --day "${day}" --score; then
    paid_ok=1
  else
    echo "WARN: fetch-cards --day ${day} failed (Free plan? 401 = credentials; 403 = upgrade Basic)" >&2
    fetch_rc=1
  fi
done

if [[ "${paid_ok}" -eq 0 ]]; then
  echo "==> fallback: Free tier /v1/racecards/free"
  if [[ -f "${BET}/scripts/vps_racing_fetch_free_tier.sh" ]]; then
    bash "${BET}/scripts/vps_racing_fetch_free_tier.sh" || fetch_rc=1
  else
    echo "ERROR: paid fetch failed and vps_racing_fetch_free_tier.sh missing" >&2
    fetch_rc=1
  fi
fi

if [[ "${fetch_rc}" -ne 0 && "${paid_ok}" -eq 0 ]]; then
  exit 1
fi

echo "==> backfill last ${RACING_FORM_RUNS:-6} runs form (raceform → upcoming_runners)"
if [[ -f "${BET}/scripts/racing_backfill_ranker_form_sqlite.py" && -f "${APP}/data/feature_store.sqlite" ]]; then
  python3 "${BET}/scripts/racing_backfill_ranker_form_sqlite.py" \
    "${APP}/data/feature_store.sqlite" --raceform "${RF}" --runs "${RACING_FORM_RUNS:-6}" || {
    echo "WARN: form backfill failed — Form column may show —" >&2
  }
fi

racing_vps_fix_data_permissions "${APP}" 2>/dev/null || true
if type racing_vps_restart_and_wait &>/dev/null; then
  racing_vps_fix_systemd_wsgi "${APP}" "${BET}" 2>/dev/null || true
  racing_vps_restart_and_wait 90 60 || {
    racing_vps_diagnose_ping_fail "${APP}"
    exit 1
  }
else
  systemctl start hibs-racing
  sleep 30
  racing_vps_wait_ping 60 3 || journalctl -u hibs-racing -n 20 --no-pager
fi

if racing_vps_sqlite_has_cards "${APP}"; then
  echo "OK: sqlite has card rows; site will show horses after ping warmup (~30s)"
  exit 0
fi
echo "ERROR: sqlite has no card rows after fetch — check Racing API / logs" >&2
exit 1
