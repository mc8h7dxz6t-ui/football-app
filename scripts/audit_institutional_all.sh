#!/usr/bin/env bash
# Unified Inst++ audit — FVE health + football/racing evidence (when hibs-bet scripts present).
#
#   ./scripts/audit_institutional_all.sh
#   FVE_API_URL=http://127.0.0.1:8000 HIBS_PRODUCTION_URL=https://hibs-bet.co.uk ./scripts/audit_institutional_all.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FVE_API="${FVE_API_URL:-http://localhost:8000}"
HIBS_ROOT="${HIBS_BET_ROOT:-${ROOT}/hibs-bet}"
FAIL=0

section() { echo ""; echo "=== $* ==="; }
ok() { echo "OK   $*"; }
fail() { echo "FAIL $*"; FAIL=1; }
warn() { echo "WARN $*"; }

section "FVE /health"
if curl -sf "${FVE_API}/health" -o /tmp/fve_audit_health.json; then
  python3 - <<'PY'
import json
h = json.load(open("/tmp/fve_audit_health.json"))
print(f"  cache={h.get('cache_backend')} bus={h.get('line_bus')} codec={h.get('wire_codec')}")
ws = h.get("ws") or {}
if ws:
    print(f"  ws_clients={ws.get('active_clients')} drops={ws.get('backpressure_drops')} bus_60s={ws.get('bus_messages_per_sec_60s')}")
else:
    print("  ws metrics: (not exposed — upgrade API)")
budgets = (h.get("api_budgets") or {}).get("sources") or {}
for src, b in budgets.items():
    rem = (b or {}).get("remaining")
    if rem is not None and rem <= 0:
        raise SystemExit(f"budget exhausted: {src}")
PY
  ok "FVE API reachable"
else
  fail "FVE API ${FVE_API}/health"
fi

section "FVE preflight"
if [[ -x "${ROOT}/scripts/preflight_fve.sh" ]]; then
  FVE_API_URL="${FVE_API}" "${ROOT}/scripts/preflight_fve.sh" || FAIL=1
else
  warn "scripts/preflight_fve.sh missing"
fi

section "Football evidence gates"
if [[ -x "${HIBS_ROOT}/scripts/verify_football_evidence_gates.sh" ]]; then
  "${HIBS_ROOT}/scripts/verify_football_evidence_gates.sh" || FAIL=1
elif [[ -d "${HIBS_ROOT}/scripts" ]]; then
  warn "hibs-bet present but verify_football_evidence_gates.sh not executable"
else
  warn "hibs-bet not in workspace — skip football evidence"
fi

section "Racing evidence gates"
if [[ -x "${HIBS_ROOT}/scripts/verify_racing_evidence_gates.sh" ]]; then
  "${HIBS_ROOT}/scripts/verify_racing_evidence_gates.sh" || FAIL=1
elif [[ -d "${HIBS_ROOT}/scripts" ]]; then
  warn "verify_racing_evidence_gates.sh not found"
else
  warn "hibs-bet not in workspace — skip racing evidence"
fi

section "FVE prematch paper ledger"
if curl -sf "${FVE_API}/health" -o /tmp/fve_audit_health.json 2>/dev/null; then
  python3 - <<'PY'
import json, sys
h = json.load(open("/tmp/fve_audit_health.json"))
paper = h.get("paper") or {}
pe = h.get("prematch_evidence") or {}
n = paper.get("with_verification_hash") or paper.get("n_rows") or 0
print(f"  paper_n={n} recon_clean={paper.get('recon_clean')} grade={pe.get('evidence_grade')}")
if paper.get("enabled") is False and paper.get("error"):
    print(f"  WARN paper ledger: {paper.get('error')}")
elif int(n or 0) < 25:
    print("  WARN prematch paper n<25 — enable FVE_PAPER_LEDGER=1 and run value-scan")
PY
  ok "FVE paper health slice present"
else
  warn "FVE health unavailable for paper check"
fi

section "FVE prematch evidence gates"
if [[ -x "${HIBS_ROOT}/scripts/verify_fve_evidence_gates.sh" ]]; then
  FVE_API_URL="${FVE_API}" "${HIBS_ROOT}/scripts/verify_fve_evidence_gates.sh" || FAIL=1
elif [[ -d "${HIBS_ROOT}/scripts" ]]; then
  warn "verify_fve_evidence_gates.sh not found"
else
  warn "hibs-bet not in workspace — skip FVE prematch evidence"
fi

section "In-play evidence gates"
if [[ -x "${HIBS_ROOT}/scripts/verify_inplay_evidence_gates.sh" ]]; then
  "${HIBS_ROOT}/scripts/verify_inplay_evidence_gates.sh" || FAIL=1
elif [[ -d "${HIBS_ROOT}/scripts" ]]; then
  warn "verify_inplay_evidence_gates.sh not found"
else
  warn "hibs-bet not in workspace — skip inplay evidence"
fi

section "FVE scrape stack"
if [[ -x "${ROOT}/scripts/vps_unpause_fve_scrape_stack.sh" ]]; then
  if curl -sf "${FVE_API}/health" -o /tmp/fve_scrape_check.json 2>/dev/null; then
    python3 - <<'PY'
import json
h = json.load(open("/tmp/fve_scrape_check.json"))
fc = h.get("feed_chain") or {}
mode = fc.get("mode") or h.get("feed_mode") or "unknown"
paused = h.get("paused", h.get("fve_paused"))
print(f"  feed_mode={mode} paused={paused}")
if paused in (True, 1, "1"):
    raise SystemExit("FVE still paused")
PY
    ok "FVE unpaused"
  else
    warn "FVE not reachable — run scripts/vps_unpause_fve_scrape_stack.sh on VPS"
  fi
else
  warn "vps_unpause_fve_scrape_stack.sh missing"
fi

section "Stack boundaries"
if [[ -x "${HIBS_ROOT}/scripts/verify_stack_boundaries.sh" ]]; then
  "${HIBS_ROOT}/scripts/verify_stack_boundaries.sh" || FAIL=1
else
  warn "verify_stack_boundaries.sh not in workspace"
fi

echo ""
if [[ "$FAIL" -eq 0 ]]; then
  echo "=== Inst++ audit PASS ==="
else
  echo "=== Inst++ audit FAIL ==="
  exit 1
fi
