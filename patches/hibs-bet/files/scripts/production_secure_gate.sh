#!/usr/bin/env bash
# Production Secure gate — engineering + traffic + honest commercial claim caps.
#
#   cd /opt/hibs-bet && bash scripts/production_secure_gate.sh
#   bash scripts/production_secure_gate.sh --strict   # exit 1 on blocking issues
#   bash scripts/production_secure_gate.sh --json
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
PY="${ROOT}/.venv/bin/python3"
[[ -x "${PY}" ]] || PY="python3"

STRICT=0
JSON=0
for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    --json) JSON=1 ;;
  esac
done

export PYTHONPATH="${ROOT}/src"
export HIBS_PRODUCTION="${HIBS_PRODUCTION:-1}"

if [[ "$JSON" -eq 1 ]]; then
  "${PY}" -c "
from hibs_predictor.production_secure import production_secure_dict
import json
print(json.dumps(production_secure_dict(), indent=2, default=str))
"
  exit 0
fi

"${PY}" -c "
from hibs_predictor.production_secure import production_secure_dict, validate_production_secure
import json, sys
rep = production_secure_dict()
print('secure=', rep.get('secure'))
print('engineering_secure=', rep.get('engineering_secure'))
print('traffic_safe=', rep.get('traffic_safe'))
print('buyer_ready=', rep.get('buyer_ready'))
claims = rep.get('commercial_claims') or {}
iv = claims.get('implied_valuation_gbp') or {}
print('implied_valuation_allowed=', iv.get('allowed'))
for c in rep.get('checks') or []:
    mark = 'PASS' if c.get('pass') else 'FAIL'
    blk = ' [blocking]' if c.get('blocking') and not c.get('pass') else ''
    print(f\"  {mark} {c.get('id')}: {c.get('label')}{blk}\")
for msg in rep.get('blocking_issues') or []:
    print('BLOCK:', msg)
for msg in rep.get('warnings') or []:
    print('WARN:', msg)
if ${STRICT}:
    validate_production_secure(strict=True)
"
