#!/usr/bin/env bash
# Light concurrent load on FVE /health + /lines — proves backpressure path, not HFT.
set -euo pipefail

API="${FVE_API_URL:-http://localhost:8000}"
N="${FVE_SOAK_CONCURRENCY:-16}"
REQS="${FVE_SOAK_REQUESTS:-64}"
FIXTURE="${FVE_SOAK_FIXTURE:-}"

echo "=== FVE load soak concurrency=${N} requests=${REQS} ==="

fail=0
for i in $(seq 1 "$REQS"); do
  (
    if ! curl -sf "${API}/health" -o /dev/null; then
      echo "FAIL health #$i" >&2
      exit 1
    fi
  ) &
  if (( i % N == 0 )); then wait || fail=1; fi
done
wait || fail=1

if [[ -n "$FIXTURE" ]]; then
  enc="${FIXTURE// /%20}"
  for i in $(seq 1 8); do
    curl -sf "${API}/lines/${enc}" -o /dev/null || fail=1 &
  done
  wait || fail=1
fi

if [[ "$fail" -eq 0 ]]; then
  echo "=== FVE soak PASS ==="
else
  echo "=== FVE soak FAIL ==="
  exit 1
fi
