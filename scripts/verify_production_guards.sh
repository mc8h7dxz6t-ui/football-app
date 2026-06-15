#!/usr/bin/env bash
# Verify feature_store_write_guard is wired in production (run on VPS).
#
# Checks:
#   1. Guard script exists and is executable
#   2. www-data (or current user) crontab references feature_store_write_guard
#      OR daily_refresh is invoked only via the guard wrapper
#   3. verification.cron.env defines RACING_FEATURE_STORE_LOCK_WAIT_SEC
#
# Usage:
#   bash scripts/verify_production_guards.sh
#   CRON_USER=www-data HIBS_RACING_DEPLOY_PATH=/opt/hibs-racing bash scripts/verify_production_guards.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUARD="${FVE_METRICS_ROOT:-${ROOT}}/scripts/feature_store_write_guard.sh"
DEPLOY="${HIBS_RACING_DEPLOY_PATH:-/opt/hibs-racing}"
DB="${HIBS_RACING_FEATURE_STORE:-${DEPLOY}/data/feature_store.sqlite}"
LOCK="${HIBS_RACING_FEATURE_STORE_LOCK:-${DB}.lock}"
ENV_FILE="${RACING_VERIFICATION_ENV_FILE:-${DEPLOY}/config/verification.cron.env}"
CRON_USER="${CRON_USER:-www-data}"

pass=0
fail=0
warn=0

ok() { echo "  OK   $*"; pass=$((pass + 1)); }
bad() { echo "  FAIL $*"; fail=$((fail + 1)); }
note() { echo "  WARN $*"; warn=$((warn + 1)); }

echo "=== feature_store production guard verification ==="
echo "Guard script: ${GUARD}"
echo "Feature DB:   ${DB}"
echo "Lock file:    ${LOCK}"
echo "Cron user:    ${CRON_USER}"
echo ""

# 1. Guard script
if [[ -x "${GUARD}" ]]; then
  ok "feature_store_write_guard.sh is executable"
else
  bad "feature_store_write_guard.sh missing or not executable: ${GUARD}"
fi

# 2. Env file
if [[ -f "${ENV_FILE}" ]]; then
  ok "verification cron env exists: ${ENV_FILE}"
  if grep -qE '^RACING_FEATURE_STORE_LOCK_WAIT_SEC=' "${ENV_FILE}"; then
    ok "RACING_FEATURE_STORE_LOCK_WAIT_SEC set in ${ENV_FILE}"
    grep '^RACING_FEATURE_STORE_LOCK_WAIT_SEC=' "${ENV_FILE}" | sed 's/^/        /'
  else
    bad "RACING_FEATURE_STORE_LOCK_WAIT_SEC missing in ${ENV_FILE}"
  fi
else
  note "verification cron env not found (copy deploy/racing-verification.cron.env.example): ${ENV_FILE}"
fi

# 3. Crontab
CRON_BODY=""
if [[ "${CRON_USER}" == "$(whoami 2>/dev/null || echo root)" ]]; then
  CRON_BODY="$(crontab -l 2>/dev/null || true)"
elif command -v sudo >/dev/null 2>&1; then
  CRON_BODY="$(sudo crontab -u "${CRON_USER}" -l 2>/dev/null || true)"
else
  note "cannot read crontab for ${CRON_USER} (no sudo)"
fi

if [[ -n "${CRON_BODY}" ]]; then
  if echo "${CRON_BODY}" | grep -q 'feature_store_write_guard'; then
    ok "crontab (${CRON_USER}) references feature_store_write_guard.sh"
    echo "${CRON_BODY}" | grep 'feature_store_write_guard' | sed 's/^/        /'
  else
    bad "crontab (${CRON_USER}) has NO feature_store_write_guard.sh entry"
  fi

  if echo "${CRON_BODY}" | grep -qE 'daily_refresh|racing_verification_automation'; then
    echo "  --- related cron lines ---"
    echo "${CRON_BODY}" | grep -E 'daily_refresh|racing_verification_automation|feature_store_write_guard|hibs-racing' | sed 's/^/        /' || true
  fi

  if echo "${CRON_BODY}" | grep -q 'daily_refresh' && ! echo "${CRON_BODY}" | grep -q 'feature_store_write_guard'; then
    bad "daily_refresh appears in crontab WITHOUT feature_store_write_guard wrapper"
  fi
else
  bad "empty or unreadable crontab for ${CRON_USER} — guard not attached"
fi

# 4. Lock path resolvable
if [[ -d "$(dirname "${DB}")" ]]; then
  ok "database directory exists: $(dirname "${DB}")"
  ls -la "$(dirname "${DB}")" 2>/dev/null | grep -E 'feature_store\.sqlite(\.lock)?' | sed 's/^/        /' || note "feature_store.sqlite not present yet"
else
  note "database directory missing: $(dirname "${DB}")"
fi

echo ""
echo "=== summary ==="
echo "  passed: ${pass}  failed: ${fail}  warnings: ${warn}"
if [[ "${fail}" -gt 0 ]]; then
  echo ""
  echo "Remediation:"
  echo "  1. Copy deploy/racing-verification.cron.env.example → ${ENV_FILE}"
  echo "  2. Wire daily_refresh via guard (hibs-bet deploy/cron-hibs-racing-daily.sh):"
  echo "       source deploy/cron-hibs-racing-daily-guard.sh"
  echo "  3. Or: sudo bash scripts/install_daily_refresh_guard_cron.sh"
  exit 1
fi
exit 0
