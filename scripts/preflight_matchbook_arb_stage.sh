#!/usr/bin/env bash
# Audit FVE Matchbook arb ladder stage from .env (read-only).
# See docs/ARB_FREEZE.md — does not change env or submit orders.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENV_FILE="${FVE_ENV_FILE:-${ROOT}/.env}"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }
info() { echo "INFO: $*"; }

env_val() {
  local key="$1" default="${2:-}"
  if [[ -f "${ENV_FILE}" ]] && grep -qE "^${key}=" "${ENV_FILE}" 2>/dev/null; then
    grep -E "^${key}=" "${ENV_FILE}" | tail -1 | cut -d= -f2- | tr -d '\r'
  else
    echo "${default}"
  fi
}

bool_on() {
  local v="${1:-}"
  [[ "${v}" == "1" || "${v}" == "true" || "${v}" == "yes" || "${v}" == "on" ]]
}

echo "=== FVE Matchbook arb stage audit ==="
echo "env: ${ENV_FILE}"
echo "utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

user="$(env_val MATCHBOOK_USERNAME)"
pass="$(env_val MATCHBOOK_PASSWORD)"
if [[ -z "${user}" || -z "${pass}" ]]; then
  fail "MATCHBOOK_USERNAME/PASSWORD missing in ${ENV_FILE}"
fi
pass "Matchbook credentials present"

paused="$(env_val FVE_PAUSED 1)"
arb_only="$(env_val FVE_ARB_ONLY 0)"
kill="$(env_val MATCHBOOK_KILL_SWITCH 0)"
auto="$(env_val MATCHBOOK_AUTO_TRADE 0)"
confirm="$(env_val MATCHBOOK_CONFIRM_LIVE)"
partial="$(env_val MATCHBOOK_ALLOW_PARTIAL_DUTCH 0)"

stage="unknown"
if bool_on "${paused}" && bool_on "${arb_only}" && bool_on "${kill}"; then
  stage="1-shadow"
elif bool_on "${paused}" && ! bool_on "${kill}" && ! bool_on "${auto}"; then
  stage="2-dry-run"
elif bool_on "${auto}" && [[ "${confirm}" == "YES" ]] && ! bool_on "${kill}"; then
  stage="3-micro-live"
elif ! bool_on "${paused}"; then
  stage="4-full-ingest"
else
  stage="custom"
fi

info "detected stage: ${stage}"
info "FVE_PAUSED=${paused} FVE_ARB_ONLY=${arb_only} KILL=${kill} AUTO=${auto} CONFIRM=${confirm}"

case "${stage}" in
  1-shadow)
    pass "stage 1 — shadow scan (no orders)"
    info "next: MATCHBOOK_KILL_SWITCH=0 for dry-run execute"
    ;;
  2-dry-run)
    pass "stage 2 — dry-run execute (offers built, not submitted)"
    info "next: MATCHBOOK_AUTO_TRADE=1 + MATCHBOOK_CONFIRM_LIVE=YES + funded balance for micro-live"
    ;;
  3-micro-live)
    pass "stage 3 — micro-live armed"
    info "caps: MAX_STAKE=$(env_val MATCHBOOK_MAX_STAKE 2) MAX_OUTLAY=$(env_val MATCHBOOK_MAX_OUTLAY 6)"
    if bool_on "${partial}"; then
      info "MATCHBOOK_ALLOW_PARTIAL_DUTCH=1 (higher risk)"
    fi
    ;;
  4-full-ingest)
    warn_msg="stage 4 — full ingest (API-Sports quota — ensure dedicated key)"
    echo "WARN: ${warn_msg}" >&2
    ;;
  *)
    echo "WARN: non-standard env combo — compare to ARB_FREEZE.md ladder" >&2
    ;;
esac

if [[ -f "${ROOT}/config/matchbook_map.json" ]]; then
  pass "matchbook_map.json present"
elif [[ -f "${ROOT}/config/matchbook_map.json.example" ]]; then
  echo "WARN: copy config/matchbook_map.json.example → matchbook_map.json for FVE_ARB_ONLY" >&2
fi

API_URL="${FVE_API_URL:-http://localhost:8000}"
if curl -sf --max-time 8 "${API_URL}/health" -o /tmp/fve_arb_health.json 2>/dev/null; then
  python3 - <<'PY'
import json
h = json.load(open("/tmp/fve_arb_health.json"))
risk = h.get("execution_risk") or h.get("risk") or {}
if risk:
    print(f"INFO: live_enabled={risk.get('live_enabled')} kill={risk.get('kill_switch')}")
else:
    print("INFO: /health OK (no execution_risk block — worker may be down)")
PY
else
  echo "WARN: cannot reach ${API_URL}/health — start arb-shadow profile first" >&2
fi

echo ""
echo "VERDICT: audit complete (stage=${stage})"
if [[ "${stage}" == "3-micro-live" ]]; then
  exit 0
fi
exit 2
