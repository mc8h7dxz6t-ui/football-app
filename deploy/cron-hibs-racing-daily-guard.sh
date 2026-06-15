#!/usr/bin/env bash
# Runnable guard wrapper for hibs-racing daily_refresh (source from cron-hibs-racing-daily.sh).
#
# In hibs-bet deploy/cron-hibs-racing-daily.sh, replace bare daily_refresh with:
#
#   FVE_METRICS_ROOT=/opt/football-app
#   HIBS_RACING_DEPLOY_PATH=/opt/hibs-racing
#   source /opt/football-app/deploy/cron-hibs-racing-daily-guard.sh
#   run_daily_refresh_guarded
#
set -euo pipefail

FVE_METRICS_ROOT="${FVE_METRICS_ROOT:-/opt/football-app}"
HIBS_RACING_DEPLOY_PATH="${HIBS_RACING_DEPLOY_PATH:-/opt/hibs-racing}"
HIBS_RACING_FEATURE_STORE="${HIBS_RACING_FEATURE_STORE:-${HIBS_RACING_DEPLOY_PATH}/data/feature_store.sqlite}"
GUARD="${FVE_METRICS_ROOT}/scripts/feature_store_write_guard.sh"
DAILY_REFRESH_CMD="${DAILY_REFRESH_CMD:-${HIBS_RACING_DEPLOY_PATH}/daily_refresh.sh --score}"
DAILY_REFRESH_USER="${DAILY_REFRESH_USER:-www-data}"

run_daily_refresh_guarded() {
  if [[ ! -x "${GUARD}" ]]; then
    echo "cron-hibs-racing-daily-guard: guard not executable: ${GUARD}" >&2
    return 1
  fi
  export HIBS_RACING_FEATURE_STORE
  export HIBS_RACING_DEPLOY_PATH
  if [[ -f "${HIBS_RACING_DEPLOY_PATH}/config/verification.cron.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${HIBS_RACING_DEPLOY_PATH}/config/verification.cron.env"
    set +a
  fi
  if [[ "$(id -un)" == "${DAILY_REFRESH_USER}" ]]; then
    exec "${GUARD}" bash -lc "cd '${HIBS_RACING_DEPLOY_PATH}' && ${DAILY_REFRESH_CMD}"
  fi
  exec "${GUARD}" sudo -u "${DAILY_REFRESH_USER}" bash -lc "cd '${HIBS_RACING_DEPLOY_PATH}' && ${DAILY_REFRESH_CMD}"
}

run_verification_automation_guarded() {
  local script="${FVE_METRICS_ROOT}/scripts/racing_verification_automation.sh"
  local log="${RACING_VERIFICATION_LOG:-${HIBS_RACING_DEPLOY_PATH}/logs/verification-automation.log}"
  if [[ ! -x "${script}" ]]; then
    echo "cron-hibs-racing-daily-guard: missing ${script}" >&2
    return 0
  fi
  mkdir -p "$(dirname "${log}")"
  FVE_METRICS_ROOT="${FVE_METRICS_ROOT}" \
  HIBS_RACING_DEPLOY_PATH="${HIBS_RACING_DEPLOY_PATH}" \
    bash "${script}" --run >> "${log}" 2>&1 || true
}
