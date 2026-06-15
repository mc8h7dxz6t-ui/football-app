#!/usr/bin/env bash
# VPS: fix racing cards (raceform.db) + www-data cron sudo.
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/cursor/racing-raceform-cron-sudo-c4a1/scripts/vps_fix_racing_raceform_cron.sh | sudo bash
#
# After uploading raceform from Mac:
#   scp ~/Downloads/raceform.db root@77.68.89.73:/opt/hibs-racing/data/raceform.db
#   curl -fsSL .../vps_fix_racing_raceform_cron.sh | sudo bash -s -- --refresh
set -euo pipefail

APP="${HIBS_BET_DEPLOY_PATH:-/opt/hibs-bet}"
RACING="${HIBS_RACING_DEPLOY_PATH:-/opt/hibs-racing}"
DO_REFRESH=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --refresh) DO_REFRESH=1 ;;
    -h|--help)
      echo "Usage: $0 [--refresh]"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
  shift
done

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "ERROR: run as root" >&2; exit 1; }

step() { echo ""; echo "==> $*"; }

step "raceform.db"
mkdir -p "${RACING}/data"
dest="${RACING}/data/raceform.db"
if [[ ! -f "${dest}" ]]; then
  echo "MISSING: ${dest}"
  echo ""
  echo "Upload from Mac first:"
  echo "  scp ~/Downloads/raceform.db root@77.68.89.73:${dest}"
  echo "  # or: cd ~/hibs-betting-app && ./hibs-bet/scripts/upload_raceform_to_vps.sh"
  exit 1
fi
ls -lh "${dest}"
chown www-data:www-data "${dest}"
if grep -q '^RACEFORM_DB_PATH=' "${RACING}/.env" 2>/dev/null; then
  sed -i 's|^RACEFORM_DB_PATH=.*|RACEFORM_DB_PATH=data/raceform.db|' "${RACING}/.env"
else
  echo 'RACEFORM_DB_PATH=data/raceform.db' >> "${RACING}/.env"
fi
chown www-data:www-data "${RACING}/.env" 2>/dev/null || true

step "sync deploy helpers (if missing on VPS)"
RAW="${HIBS_BET_RAW:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/cursor/racing-raceform-cron-sudo-c4a1}"
mkdir -p "${APP}/deploy"
for rel in install-hibs-cron-sudoers.sh cron-hibs-racing-daily.sh cron-hibs-racing-watchdog.sh; do
  if [[ ! -f "${APP}/deploy/${rel}" ]]; then
    curl -fsSL "${RAW}/patches/hibs-bet/files/deploy/${rel}" -o "${APP}/deploy/${rel}"
    chmod 755 "${APP}/deploy/${rel}"
  fi
done

step "www-data cron sudo (fixes 'sudo: a password is required')"
if [[ -f "${APP}/deploy/install-hibs-cron-sudoers.sh" ]]; then
  bash "${APP}/deploy/install-hibs-cron-sudoers.sh"
else
  cat >/etc/sudoers.d/hibs-racing-cron <<EOF
www-data ALL=(ALL) NOPASSWD: ${APP}/deploy/cron-hibs-racing-daily.sh
www-data ALL=(ALL) NOPASSWD: ${APP}/deploy/cron-hibs-racing-watchdog.sh
EOF
  chmod 440 /etc/sudoers.d/hibs-racing-cron
  visudo -c -f /etc/sudoers.d/hibs-racing-cron
  echo "Installed /etc/sudoers.d/hibs-racing-cron (inline fallback)"
fi

step "reinstall racing crons"
if [[ -f "${APP}/deploy/cron-hibs-racing-daily.sh" ]]; then
  bash "${APP}/deploy/cron-hibs-racing-daily.sh" --install
fi
if [[ -f "${APP}/deploy/cron-hibs-racing-watchdog.sh" ]]; then
  bash "${APP}/deploy/cron-hibs-racing-watchdog.sh" --install
fi

if [[ "${DO_REFRESH}" -eq 1 ]]; then
  step "manual card refresh (same as cron --run)"
  bash "${APP}/deploy/cron-hibs-racing-daily.sh" --run
  echo ""
  tail -40 /var/log/hibs-racing/daily-refresh.log
fi

step "verify"
if [[ -f "${APP}/scripts/vps_racing_diagnose_cards.sh" ]]; then
  bash "${APP}/scripts/vps_racing_diagnose_cards.sh" || true
else
  echo "raceform: $(ls -lh "${dest}")"
  echo "cron sudo: $(test -f /etc/sudoers.d/hibs-racing-cron && echo OK || echo missing)"
fi

echo ""
echo "Done. Cards: https://hibs-bet.co.uk/racing/cards"
