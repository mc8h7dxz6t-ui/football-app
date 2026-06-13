#!/usr/bin/env bash
# VPS-native racing daily refresh — cards/meetings without Mac rsync.
#
#   sudo bash /opt/hibs-bet/deploy/cron-hibs-racing-daily.sh --install
#   sudo bash /opt/hibs-bet/deploy/cron-hibs-racing-daily.sh --run
set -euo pipefail

HIBS_BET_ROOT="${HIBS_BET_DEPLOY_PATH:-/opt/hibs-bet}"
RACING_ROOT="${HIBS_RACING_DEPLOY_PATH:-/opt/hibs-racing}"
LOG_DIR="${LOG_DIR:-/var/log/hibs-racing}"
LOG_FILE="${LOG_DIR}/daily-refresh.log"
MARKER="# hibs-racing: vps daily refresh"
CRON_SCRIPT="${HIBS_BET_ROOT}/deploy/cron-hibs-racing-daily.sh"

# shellcheck source=../scripts/lib_racing_vps_probe.sh
source "${HIBS_BET_ROOT}/scripts/lib_racing_vps_probe.sh"

usage() {
  echo "Usage: $0 [--print|--install|--run]"
}

run_refresh() {
  mkdir -p "${LOG_DIR}"
  export HIBS_RACING_DEPLOY_PATH="${RACING_ROOT}"
  export HIBS_BET_DEPLOY_PATH="${HIBS_BET_ROOT}"
  {
    echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) racing daily ====="
    echo "    RACING_ROOT=${RACING_ROOT} HIBS_BET_ROOT=${HIBS_BET_ROOT}"
    if [[ ! -d "${RACING_ROOT}" ]]; then
      echo "ERROR: ${RACING_ROOT} missing — deploy hibs-racing first"
      exit 1
    fi
    if [[ -f "${HIBS_BET_ROOT}/scripts/vps_racing_bootstrap.sh" ]]; then
      echo "==> preflight bootstrap (--light)"
      bash "${HIBS_BET_ROOT}/scripts/vps_racing_bootstrap.sh" --light --skip-restart || {
        echo "WARN: bootstrap preflight failed — continuing if raceform present" >&2
      }
    fi
    if ! racing_vps_repair_raceform_env "${RACING_ROOT}"; then
      echo "ERROR: raceform.db missing — scp to $(racing_vps_canonical_raceform "${RACING_ROOT}")"
      echo "  Mac: scp ~/Downloads/raceform.db root@HOST:$(racing_vps_canonical_raceform "${RACING_ROOT}")"
      exit 1
    fi
    if [[ -f "${HIBS_BET_ROOT}/scripts/vps_racing_refresh_cards_cli.sh" ]]; then
      bash "${HIBS_BET_ROOT}/scripts/vps_racing_refresh_cards_cli.sh" || exit 1
    elif [[ -f "${HIBS_BET_ROOT}/scripts/vps_racing_fix_cards.sh" ]]; then
      bash "${HIBS_BET_ROOT}/scripts/vps_racing_fix_cards.sh" || exit 1
    else
      echo "ERROR: missing vps_racing_refresh_cards_cli.sh — sync scripts from hibs-bet" >&2
      exit 1
    fi
    if systemctl is-active hibs-racing &>/dev/null; then
      echo "==> restart + smoke (ping/portfolio only)"
      racing_vps_restart_service
      racing_vps_wait_ping 45 3 || true
      racing_vps_smoke_local || echo "WARN: smoke failed after refresh"
    fi
    echo "===== done ====="
  } >>"${LOG_FILE}" 2>&1
  chown www-data:www-data "${LOG_FILE}" 2>/dev/null || true
}

install_cron() {
  mkdir -p "${LOG_DIR}"
  chown www-data:www-data "${LOG_DIR}" 2>/dev/null || true
  if [[ -f "${HIBS_BET_ROOT}/deploy/install-hibs-cron-sudoers.sh" ]]; then
    bash "${HIBS_BET_ROOT}/deploy/install-hibs-cron-sudoers.sh"
  fi
  local existing tmp
  existing="$(crontab -u www-data -l 2>/dev/null || true)"
  existing="$(printf '%s\n' "${existing}" | grep -vF "${MARKER}" | grep -vF "${CRON_SCRIPT}" | grep -vF 'cron-hibs-racing-daily.sh' || true)"
  tmp="$(mktemp)"
  {
    printf '%s\n' "${existing}"
    echo ""
    echo "${MARKER}"
    echo "5 6 * * * sudo bash ${CRON_SCRIPT} --run >> ${LOG_FILE} 2>&1"
    echo "5 12 * * * sudo bash ${CRON_SCRIPT} --run >> ${LOG_FILE} 2>&1"
    echo "5 17 * * * sudo bash ${CRON_SCRIPT} --run >> ${LOG_FILE} 2>&1"
  } >"${tmp}"
  crontab -u www-data "${tmp}"
  rm -f "${tmp}"
  echo "Installed racing VPS refresh (06:05 + 12:05 + 17:05 UTC) -> ${LOG_FILE}"
}

case "${1:---print}" in
  --install) install_cron ;;
  --run) run_refresh ;;
  --print)
    echo "${MARKER}"
    echo "5 6 * * * sudo bash ${CRON_SCRIPT} --run >> ${LOG_FILE} 2>&1"
    echo "5 12 * * * sudo bash ${CRON_SCRIPT} --run >> ${LOG_FILE} 2>&1"
    echo "5 17 * * * sudo bash ${CRON_SCRIPT} --run >> ${LOG_FILE} 2>&1"
    ;;
  -h|--help) usage ;;
  *) usage; exit 1 ;;
esac
