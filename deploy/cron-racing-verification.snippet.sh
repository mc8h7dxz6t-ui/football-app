# Append after racing_value_lane_run_full in deploy/cron-hibs-racing-daily.sh (hibs-bet):
#
# Wrap card ingest / scoring writes so they share the verification flock:
#   GUARD="${HIBS_BET_ROOT}/../football-app/scripts/feature_store_write_guard.sh"
#   if [[ -x "${GUARD}" ]]; then
#     HIBS_RACING_FEATURE_STORE="${RACING_ROOT}/data/feature_store.sqlite" \
#       "${GUARD}" sudo -u www-data bash -lc 'cd ${RACING_ROOT} && ./daily_refresh.sh --score'
#   fi
#
#   if [[ -x "${HIBS_BET_ROOT}/../football-app/scripts/racing_verification_automation.sh" ]]; then
#     FVE_METRICS_ROOT="${HIBS_BET_ROOT}/../football-app" \
#     HIBS_RACING_DEPLOY_PATH="${RACING_ROOT}" \
#     bash "${FVE_METRICS_ROOT}/scripts/racing_verification_automation.sh" --run \
#       >> "${LOG_DIR}/verification-automation.log" 2>&1 || true
#   fi
#
# Or install standalone cron:
#   sudo FVE_METRICS_ROOT=/opt/football-app HIBS_RACING_DEPLOY_PATH=/opt/hibs-racing \
#     bash /opt/football-app/scripts/racing_verification_automation.sh --install-cron
