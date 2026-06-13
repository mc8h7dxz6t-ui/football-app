#!/usr/bin/env bash
# VPS one-liner for hibs-bet inst++ F8 CLV fix (no git pull required).
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/scripts/vps_install_hibs_bet_wc_clv_f8.sh | sudo bash
#
# Or on VPS directly:
#   sudo DEPLOY_PATH=/opt/hibs-bet bash scripts/vps_install_hibs_bet_wc_clv_f8.sh
set -euo pipefail

APP="${DEPLOY_PATH:-/opt/hibs-bet}"
PATCH_URL="${HIBS_CLV_PATCH_URL:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/patches/hibs-bet/wc-clv-f8.patch}"
PY="${APP}/.venv/bin/python3"
[[ -x "${PY}" ]] || PY=python3

log() { echo "[wc-clv-f8] $*"; }
warn() { echo "[wc-clv-f8] WARN: $*" >&2; }

[[ -d "${APP}/src/hibs_predictor" ]] || {
  echo "ERROR: hibs-bet not found at ${APP}" >&2
  exit 1
}

cd "${APP}"

if grep -q 'clv_predicted_outcome_fallback' "${APP}/src/hibs_predictor/prediction_log.py" 2>/dev/null; then
  log "CLV fix already present"
else
  tmp="$(mktemp)"
  trap 'rm -f "${tmp}"' EXIT
  log "fetching patch"
  curl -fsSL "${PATCH_URL}" -o "${tmp}"
  log "applying patch in ${APP}"
  if git -C "${APP}" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "${APP}" am --3way "${tmp}" 2>/dev/null || \
      git -C "${APP}" apply --3way "${tmp}" 2>/dev/null || \
      patch -p1 <"${tmp}"
  else
    patch -p1 <"${tmp}"
  fi
fi

touch "${APP}/.env"
for kv in HIBS_CLV_LOG_ENABLED=1 HIBS_CLV_PREDICTED_OUTCOME_FALLBACK=1 HIBS_PREDICTION_LOG_ENABLED=1; do
  k="${kv%%=*}"
  grep -q "^${k}=" "${APP}/.env" 2>/dev/null || echo "${kv}" >>"${APP}/.env"
done
chown www-data:www-data "${APP}/.env" 2>/dev/null || true

n="$(grep -c 'clv_predicted_outcome_fallback' "${APP}/src/hibs_predictor/prediction_log.py" || true)"
[[ "${n}" -ge 2 ]] || { echo "ERROR: patch apply failed (grep=${n})" >&2; exit 1; }
log "code OK (grep=${n})"

export PYTHONPATH="${APP}/src"
set -a
# shellcheck disable=SC1091
source "${APP}/.env"
set +a

log "pred-log-sync"
"${PY}" -m hibs_predictor.main pred-log-sync --verbose --min-after-kickoff-hours 0 || \
  warn "pred-log-sync failed"

if [[ -f "${APP}/scripts/verify_football_evidence_gates.sh" ]]; then
  bash "${APP}/scripts/verify_football_evidence_gates.sh" || true
fi

log "done — F8 climbs as WC fixtures finish (buyer_ready needs n≥25 CLV rows)"
