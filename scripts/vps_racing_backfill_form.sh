#!/usr/bin/env bash
# Backfill last 5-6 race form into feature_store (fills cards "Form" column).
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/cursor/racing-last-form-backfill-c4a1/scripts/vps_racing_backfill_form.sh | sudo bash
set -euo pipefail

APP="${HIBS_RACING_DEPLOY_PATH:-/opt/hibs-racing}"
BET="${HIBS_BET_DEPLOY_PATH:-/opt/hibs-bet}"
RUNS="${RACING_FORM_RUNS:-6}"
RAW="${HIBS_BET_RAW:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/cursor/racing-last-form-backfill-c4a1}"

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "ERROR: run as root" >&2; exit 1; }

# shellcheck source=/dev/null
source "${BET}/scripts/lib_racing_vps_probe.sh"

mkdir -p "${BET}/scripts"
echo "==> sync racing_backfill_ranker_form_sqlite.py"
curl -fsSL "${RAW}/patches/hibs-bet/files/scripts/racing_backfill_ranker_form_sqlite.py" \
  -o "${BET}/scripts/racing_backfill_ranker_form_sqlite.py"
chmod 755 "${BET}/scripts/racing_backfill_ranker_form_sqlite.py"

racing_vps_repair_raceform_env "${APP}" || {
  echo "ERROR: raceform.db required at ${APP}/data/raceform.db" >&2
  exit 1
}
RF="$(racing_vps_resolve_raceform "${APP}")"
FS="${APP}/data/feature_store.sqlite"
[[ -f "${FS}" ]] || { echo "ERROR: missing ${FS} — run card refresh first" >&2; exit 1; }

echo "==> backfill form (last ${RUNS} runs)"
if python3 "${BET}/scripts/racing_backfill_ranker_form_sqlite.py" --help 2>&1 | grep -q -- '--runs'; then
  python3 "${BET}/scripts/racing_backfill_ranker_form_sqlite.py" "${FS}" --raceform "${RF}" --runs "${RUNS}"
else
  echo "WARN: script missing --runs flag — using legacy backfill (still last 6 in SQL)" >&2
  python3 "${BET}/scripts/racing_backfill_ranker_form_sqlite.py" "${FS}" --raceform "${RF}"
fi

chown www-data:www-data "${FS}" "${RF}" 2>/dev/null || true
systemctl restart hibs-racing
sleep 3

html="$(curl -sS --max-time 60 http://127.0.0.1:5003/cards 2>/dev/null | head -c 500000 || true)"
empty="$(echo "${html}" | grep -c 'title="RP form figures">—</td>' || true)"
filled="$(echo "${html}" | grep -cE 'title="RP form figures">[0-9FPUn-]+</td>' || true)"
echo "Form cells: filled=${filled} empty=${empty}"
echo "Done — hard-refresh https://hibs-bet.co.uk/racing/cards"
