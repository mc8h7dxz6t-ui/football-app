#!/usr/bin/env bash
# Read-only corruption firewall — run daily or before trusting the UI
set -euo pipefail
API_URL="${FVE_API_URL:-http://localhost:8000}"
FAIL=0

check() {
  local name="$1"
  shift
  if "$@"; then
    echo "OK   $name"
  else
    echo "FAIL $name"
    FAIL=1
  fi
}

echo "=== FVE preflight $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

check "API /health" curl -sf "${API_URL}/health" -o /tmp/fve_health.json
if [[ -f /tmp/fve_health.json ]]; then
  python3 - <<'PY'
import json, sys
h = json.load(open("/tmp/fve_health.json"))
budgets = (h.get("api_budgets") or {}).get("sources") or {}
for src in ("matchbook", "odds_api", "api_football"):
    b = budgets.get(src) or {}
    rem = b.get("remaining")
    cap = b.get("cap_per_hour")
    if cap and rem is not None and rem <= 0:
        print(f"FAIL budget exhausted: {src}")
        sys.exit(1)
print(f"cache={h.get('cache_backend')} bus={h.get('line_bus')}")
PY
  [[ $? -eq 0 ]] || FAIL=1
fi

if [[ -f /tmp/fve_worker_heartbeat ]]; then
  age=$(( $(date +%s) - $(cat /tmp/fve_worker_heartbeat) ))
  if [[ "$age" -lt 120 ]]; then
    echo "OK   worker heartbeat (${age}s ago)"
  else
    echo "FAIL worker heartbeat stale (${age}s)"
    FAIL=1
  fi
else
  echo "WARN no worker heartbeat (docker worker uses container /tmp)"
fi

# Sample one cached fixture if env provides key
SAMPLE="${FVE_PREFLIGHT_FIXTURE:-}"
if [[ -n "$SAMPLE" ]]; then
  if curl -sf "${API_URL}/lines/${SAMPLE// /%20}" -o /tmp/fve_lines.json; then
    ticks=$(python3 -c "import json; d=json.load(open('/tmp/fve_lines.json')); print(d.get('tick_count',0))")
    if [[ "$ticks" -gt 0 ]]; then
      echo "OK   lines cached for $SAMPLE (ticks=$ticks)"
    else
      echo "FAIL lines empty for $SAMPLE — worker may not be ingesting"
      FAIL=1
    fi
  else
    echo "FAIL no /lines for $SAMPLE"
    FAIL=1
  fi
else
  echo "INFO set FVE_PREFLIGHT_FIXTURE='Team A v Team B' to verify cached lines"
fi

if [[ "$FAIL" -eq 0 ]]; then
  echo "=== PASS ==="
else
  echo "=== FAIL — do not trust green UI ==="
  exit 1
fi
