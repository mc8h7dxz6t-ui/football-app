#!/usr/bin/env bash
# Wrap hibs-racing feature_store write jobs with the shared POSIX flock.
#
# Usage (hibs-racing daily refresh / card ingest):
#   HIBS_RACING_FEATURE_STORE=/opt/hibs-racing/data/feature_store.sqlite \
#     bash /opt/football-app/scripts/feature_store_write_guard.sh \
#       python -m hibs_racing.daily_refresh --score
#
# Lock file default: ${HIBS_RACING_FEATURE_STORE}.lock (sibling of the sqlite file)
set -euo pipefail

DB="${HIBS_RACING_FEATURE_STORE:-${HIBS_RACING_DEPLOY_PATH:-}/data/feature_store.sqlite}"
if [[ -z "${DB}" || "${DB}" == "/data/feature_store.sqlite" ]]; then
  echo "feature_store_write_guard: set HIBS_RACING_FEATURE_STORE or HIBS_RACING_DEPLOY_PATH" >&2
  exit 2
fi

LOCK="${HIBS_RACING_FEATURE_STORE_LOCK:-${DB}.lock}"
WAIT_SEC="${RACING_FEATURE_STORE_LOCK_WAIT_SEC:-60}"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <command...>" >&2
  exit 2
fi

mkdir -p "$(dirname "${LOCK}")"

exec flock -w "${WAIT_SEC}" "${LOCK}" "$@"
