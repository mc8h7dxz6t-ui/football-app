# hibs-bet: deploy/cron-hibs-racing-daily.sh should source the guarded runner (not bare daily_refresh):
#
#   FVE_METRICS_ROOT=/opt/football-app
#   HIBS_RACING_DEPLOY_PATH=/opt/hibs-racing
#   source /opt/football-app/deploy/cron-hibs-racing-daily-guard.sh
#   run_daily_refresh_guarded
#   run_verification_automation_guarded   # optional, after refresh
#
# Or install standalone www-data cron from football-app:
#   sudo FVE_METRICS_ROOT=/opt/football-app HIBS_RACING_DEPLOY_PATH=/opt/hibs-racing \
#     bash /opt/football-app/scripts/install_daily_refresh_guard_cron.sh
#
# Verify production wiring:
#   sudo CRON_USER=www-data bash /opt/football-app/scripts/verify_production_guards.sh
#
# Legacy inline form (equivalent):
#   GUARD="${HIBS_BET_ROOT}/../football-app/scripts/feature_store_write_guard.sh"
#   HIBS_RACING_FEATURE_STORE="${RACING_ROOT}/data/feature_store.sqlite" \
#     "${GUARD}" sudo -u www-data bash -lc 'cd ${RACING_ROOT} && ./daily_refresh.sh --score'
