#!/usr/bin/env bash
# Passwordless sudo for www-data racing cron wrappers (systemctl restart needs root).
#
#   sudo bash /opt/hibs-bet/deploy/install-hibs-cron-sudoers.sh
set -euo pipefail

HIBS_BET_ROOT="${HIBS_BET_DEPLOY_PATH:-/opt/hibs-bet}"
SUDOERS_FILE="/etc/sudoers.d/hibs-racing-cron"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: run as root (sudo bash $0)" >&2
  exit 1
fi

tmp="$(mktemp)"
cat >"${tmp}" <<EOF
# Managed by ${HIBS_BET_ROOT}/deploy/install-hibs-cron-sudoers.sh
# www-data crontab runs these wrappers with sudo (no TTY).
www-data ALL=(ALL) NOPASSWD: ${HIBS_BET_ROOT}/deploy/cron-hibs-racing-daily.sh
www-data ALL=(ALL) NOPASSWD: ${HIBS_BET_ROOT}/deploy/cron-hibs-racing-watchdog.sh
EOF

if ! visudo -c -f "${tmp}" >/dev/null 2>&1; then
  echo "ERROR: generated sudoers fragment failed visudo check" >&2
  rm -f "${tmp}"
  exit 1
fi

install -m 440 -o root -g root "${tmp}" "${SUDOERS_FILE}"
rm -f "${tmp}"
echo "Installed ${SUDOERS_FILE}"
