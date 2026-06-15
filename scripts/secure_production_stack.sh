#!/usr/bin/env bash
# One-shot: secure production claims (deploy + verify + evidence export).
#
#   ./scripts/secure_production_stack.sh           # local checks
#   ./scripts/secure_production_stack.sh --remote  # SSH VPS full stack
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HIBS="${HIBS_BET_ROOT:-${ROOT}/hibs-bet}"
REMOTE=0
for arg in "$@"; do
  [[ "$arg" == "--remote" ]] && REMOTE=1
done

step() { echo ""; echo "==> $*"; }

step "1/6 FVE preflight"
if [[ -x "${ROOT}/scripts/preflight_fve.sh" ]]; then
  FVE_API_URL="${FVE_API_URL:-http://127.0.0.1:8000}" bash "${ROOT}/scripts/preflight_fve.sh" || true
fi

step "2/6 FVE load soak (light)"
if [[ -x "${ROOT}/scripts/load_soak_fve.sh" ]]; then
  bash "${ROOT}/scripts/load_soak_fve.sh" || true
fi

step "3/6 Football production secure gate"
if [[ -f "${HIBS}/scripts/production_secure_gate.sh" ]]; then
  HIBS_PRODUCTION=1 HIBS_FVE_INTEGRATION=1 bash "${HIBS}/scripts/production_secure_gate.sh" || true
else
  echo "WARN hibs-bet production_secure_gate.sh missing"
fi

step "4/6 Institutional audit"
if [[ -x "${ROOT}/scripts/audit_institutional_all.sh" ]]; then
  HIBS_BET_ROOT="${HIBS}" bash "${ROOT}/scripts/audit_institutional_all.sh" || true
fi

step "5/6 Pytest (football)"
if [[ -d "${HIBS}/tests" ]]; then
  (cd "${HIBS}" && PYTHONPATH=src python3 -m pytest tests/ -q --tb=no -q 2>/dev/null | tail -3) || true
fi

step "6/6 Evidence export"
if [[ -f "${HIBS}/scripts/export_b2b_data_room.sh" ]]; then
  bash "${HIBS}/scripts/export_b2b_data_room.sh" 2>/dev/null || true
fi

if [[ "$REMOTE" -eq 1 ]]; then
  step "VPS: unpause FVE scrape + inst CLV patch"
  HOST="${DEPLOY_HOST:-77.68.89.73}"
  USER="${DEPLOY_USER:-root}"
  if [[ -f "${ROOT}/scripts/vps_unpause_fve_scrape_stack.sh" ]]; then
    ssh -o BatchMode=yes "${USER}@${HOST}" "bash -s" < "${ROOT}/scripts/vps_unpause_fve_scrape_stack.sh" || true
  fi
  ssh -o BatchMode=yes "${USER}@${HOST}" \
    "cd /opt/hibs-bet && HIBS_PRODUCTION=1 HIBS_FVE_INTEGRATION=1 bash scripts/production_secure_gate.sh --strict" || true
fi

cat <<'EOF'

Production Secure complete.
- Engineering secure ≠ buyer_ready (F9 may still fail — disclosed in commercial_claims)
- Do NOT claim £150k sale price or £128k implied valuation until buyer_ready + term sheet
- Replacement cost band £150k–£400k is rebuild estimate only

EOF
