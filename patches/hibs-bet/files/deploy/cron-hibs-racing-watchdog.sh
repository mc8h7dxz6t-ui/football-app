#!/usr/bin/env bash
# Racing health watchdog: restart hibs-racing when /api/ping stops responding.
#
#   sudo bash /opt/hibs-bet/deploy/cron-hibs-racing-watchdog.sh --install
#   sudo bash /opt/hibs-bet/deploy/cron-hibs-racing-watchdog.sh --run
set -euo pipefail

HIBS_BET_ROOT="${HIBS_BET_DEPLOY_PATH:-/opt/hibs-bet}"
LOG_DIR="${LOG_DIR:-/var/log/hibs-racing}"
LOG_FILE="${LOG_DIR}/watchdog.log"
MARKER="# hibs-racing: ping watchdog"
SCRIPT="${HIBS_BET_ROOT}/deploy/cron-hibs-racing-watchdog.sh"
STATE_DIR="${LOG_DIR}/.watchdog"
FAIL_FILE="${STATE_DIR}/fail_count"

# shellcheck source=../scripts/lib_racing_vps_probe.sh
source "${HIBS_BET_ROOT}/scripts/lib_racing_vps_probe.sh"

usage() {
  echo "Usage: $0 [--print|--install|--run]"
}

run_watchdog() {
  mkdir -p "${LOG_DIR}" "${STATE_DIR}"
  local code fails
  code="$(racing_vps_http_code "${RACING_VPS_PING_URL}" 8)"
  if [[ "${code}" == "200" ]]; then
    echo 0 >"${FAIL_FILE}"
    exit 0
  fi
  fails="$(cat "${FAIL_FILE}" 2>/dev/null || echo 0)"
  fails=$((fails + 1))
  echo "${fails}" >"${FAIL_FILE}"
  {
    echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) ping=${code} fail_streak=${fails} ====="
  } >>"${LOG_FILE}"
  if [[ "${fails}" -lt 2 ]]; then
    exit 0
  fi
  {
    echo "restarting hibs-racing after ${fails} failed pings"
    racing_vps_restart_service
    if racing_vps_wait_ping 40 4; then
      echo 0 >"${FAIL_FILE}"
      racing_vps_smoke_local || true
      echo "recovery ok"
    else
      journalctl -u hibs-racing -n 15 --no-pager || true
      echo "recovery failed"
    fi
  } >>"${LOG_FILE}" 2>&1
  chown -R www-data:www-data "${LOG_DIR}" 2>/dev/null || true
}

install_cron() {
  mkdir -p "${LOG_DIR}"
  if [[ -f "${HIBS_BET_ROOT}/deploy/install-hibs-cron-sudoers.sh" ]]; then
    bash "${HIBS_BET_ROOT}/deploy/install-hibs-cron-sudoers.sh"
  fi
  local existing tmp
  existing="$(crontab -u www-data -l 2>/dev/null || true)"
  existing="$(printf '%s\n' "${existing}" | grep -vF "${MARKER}" | grep -vF "${SCRIPT}" | grep -vF 'cron-hibs-racing-watchdog.sh' || true)"
  tmp="$(mktemp)"
  {
    printf '%s\n' "${existing}"
    echo ""
    echo "${MARKER}"
    echo "*/15 * * * * sudo bash ${SCRIPT} --run >> ${LOG_FILE} 2>&1"
  } >"${tmp}"
  crontab -u www-data "${tmp}"
  rm -f "${tmp}"
  echo "Installed racing watchdog (every 15 min) -> ${LOG_FILE}"
}

case "${1:---print}" in
  --install) install_cron ;;
  --run) run_watchdog ;;
  --print)
    echo "${MARKER}"
    echo "*/15 * * * * sudo bash ${SCRIPT} --run >> ${LOG_FILE} 2>&1"
    ;;
  -h|--help) usage ;;
  *) usage; exit 1 ;;
esac
