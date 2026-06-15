#!/usr/bin/env bash
# Install www-data cron: daily_refresh wrapped with feature_store_write_guard.
#
# Prerequisite: hibs-racing deployed at HIBS_RACING_DEPLOY_PATH with daily_refresh.sh
#
#   sudo FVE_METRICS_ROOT=/opt/football-app \
#        HIBS_RACING_DEPLOY_PATH=/opt/hibs-racing \
#        bash scripts/install_daily_refresh_guard_cron.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FVE_METRICS_ROOT="${FVE_METRICS_ROOT:-${ROOT}}"
HIBS_RACING_DEPLOY_PATH="${HIBS_RACING_DEPLOY_PATH:-/opt/hibs-racing}"
CRON_USER="${CRON_USER:-www-data}"
MARK="# hibs-racing-daily-refresh-guarded"
GUARD="${FVE_METRICS_ROOT}/scripts/feature_store_write_guard.sh"
ENV_FILE="${HIBS_RACING_DEPLOY_PATH}/config/verification.cron.env"

if [[ ! -x "${GUARD}" ]]; then
  echo "missing executable guard: ${GUARD}" >&2
  exit 1
fi

if [[ ! -f "${HIBS_RACING_DEPLOY_PATH}/daily_refresh.sh" ]] && [[ ! -f "${HIBS_RACING_DEPLOY_PATH}/scripts/daily_refresh.sh" ]]; then
  echo "warn: daily_refresh.sh not found under ${HIBS_RACING_DEPLOY_PATH} — cron will still install" >&2
fi

mkdir -p "${HIBS_RACING_DEPLOY_PATH}/config" "${HIBS_RACING_DEPLOY_PATH}/logs"
if [[ ! -f "${ENV_FILE}" ]] && [[ -f "${ROOT}/deploy/racing-verification.cron.env.example" ]]; then
  cp "${ROOT}/deploy/racing-verification.cron.env.example" "${ENV_FILE}"
  echo "installed ${ENV_FILE} from example"
fi

# 06:05 UTC — before verification at :20 (card refresh then settle pipeline)
CRON_CMD="bash -lc 'set -a; [ -f ${ENV_FILE} ] && . ${ENV_FILE}; set +a; \
FVE_METRICS_ROOT=${FVE_METRICS_ROOT} HIBS_RACING_DEPLOY_PATH=${HIBS_RACING_DEPLOY_PATH} \
source ${ROOT}/deploy/cron-hibs-racing-daily-guard.sh && run_daily_refresh_guarded' \
>> ${HIBS_RACING_DEPLOY_PATH}/logs/daily-refresh.log 2>&1"

LINE="5 6 * * * ${CRON_CMD} ${MARK}"

list_cmd=(crontab -l)
install_cmd=(crontab -)
if [[ "${CRON_USER}" != "$(whoami)" ]]; then
  list_cmd=(crontab -u "${CRON_USER}" -l)
  install_cmd=(crontab -u "${CRON_USER}" -)
fi

existing="$("${list_cmd[@]}" 2>/dev/null || true)"
filtered="$(echo "${existing}" | grep -v "${MARK}" | grep -v 'feature_store_write_guard.*daily_refresh' || true)"
printf '%s\n%s\n' "${filtered}" "${LINE}" | "${install_cmd[@]}"

echo "Installed guarded daily_refresh for ${CRON_USER} (06:05 UTC)"
echo "Verify: bash ${ROOT}/scripts/verify_production_guards.sh"
