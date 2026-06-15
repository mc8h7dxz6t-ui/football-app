#!/usr/bin/env bash
# Robust racing verification — flock, env resolution, non-fatal thin windows.
#
# VPS (after hibs-racing daily refresh):
#   FVE_METRICS_ROOT=/opt/football-app \
#   HIBS_RACING_DEPLOY_PATH=/opt/hibs-racing \
#   bash scripts/racing_verification_automation.sh --run
#
# Install cron (3× daily at :20, after card refresh :05):
#   sudo bash scripts/racing_verification_automation.sh --install-cron
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export FVE_METRICS_ROOT="${FVE_METRICS_ROOT:-${ROOT}}"
export HIBS_RACING_DEPLOY_PATH="${HIBS_RACING_DEPLOY_PATH:-${HOME}/hibs-racing}"
export HIBS_RACING_FEATURE_STORE="${HIBS_RACING_FEATURE_STORE:-${HIBS_RACING_DEPLOY_PATH}/data/feature_store.sqlite}"
export RACING_VERIFICATION_JSONL="${RACING_VERIFICATION_JSONL:-${HIBS_RACING_DEPLOY_PATH}/data/verification/settled_races.jsonl}"
export RACING_VERIFICATION_LOG="${RACING_VERIFICATION_LOG:-/var/log/hibs-racing/verification-automation.log}"

PY="${FVE_METRICS_ROOT}/.venv/bin/python3"
[[ -x "${PY}" ]] || PY="python3"

run_py() {
  cd "${FVE_METRICS_ROOT}"
  exec "${PY}" scripts/racing_verification_automation.py "$@"
}

case "${1:---help}" in
  --run)
    shift
    mkdir -p "$(dirname "${RACING_VERIFICATION_LOG}")" 2>/dev/null || true
    run_py --run "$@"
    ;;
  --install-cron)
    shift
    run_py --install-cron "$@"
    ;;
  -h|--help)
    echo "Usage: $0 --run | --install-cron"
    echo "Env: FVE_METRICS_ROOT HIBS_RACING_DEPLOY_PATH HIBS_RACING_FEATURE_STORE"
    ;;
  *)
    run_py "$@"
    ;;
esac
