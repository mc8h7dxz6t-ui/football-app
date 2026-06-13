#!/usr/bin/env bash
# VPS one-liner for hibs-bet inst++ F8 CLV fix (no git pull required).
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/scripts/vps_install_hibs_bet_wc_clv_f8.sh | sudo bash
#
# Or on VPS directly:
#   sudo DEPLOY_PATH=/opt/hibs-bet bash scripts/vps_install_hibs_bet_wc_clv_f8.sh
set -euo pipefail

APP="${DEPLOY_PATH:-/opt/hibs-bet}"
PATCH_CORE_URL="${HIBS_CLV_PATCH_URL:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/patches/hibs-bet/wc-clv-f8-core.patch}"
PATCH_FULL_URL="${HIBS_CLV_PATCH_FULL_URL:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/patches/hibs-bet/wc-clv-f8.patch}"
PY="${APP}/.venv/bin/python3"
[[ -x "${PY}" ]] || PY=python3

log() { echo "[wc-clv-f8] $*"; }
warn() { echo "[wc-clv-f8] WARN: $*" >&2; }

[[ -d "${APP}/src/hibs_predictor" ]] || {
  echo "ERROR: hibs-bet not found at ${APP}" >&2
  exit 1
}

cd "${APP}"

code_ok() {
  local n
  n="$(grep -c 'clv_predicted_outcome_fallback' "${APP}/src/hibs_predictor/prediction_log.py" 2>/dev/null || true)"
  [[ "${n}" -ge 2 ]]
}

apply_patch_file() {
  local patch_file="$1"
  log "applying ${patch_file}"
  if git -C "${APP}" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "${APP}" apply --3way "${patch_file}" 2>/dev/null && return 0
  fi
  # patch exits 1 when optional hunks fail (.env.example drift) — verify core files instead
  patch -p1 --forward <"${patch_file}" || true
  find "${APP}" -name '*.rej' -delete 2>/dev/null || true
}

if ! code_ok; then
  tmp="$(mktemp)"
  trap 'rm -f "${tmp}"' EXIT
  log "fetching core patch (skips .env.example)"
  if ! curl -fsSL "${PATCH_CORE_URL}" -o "${tmp}" 2>/dev/null; then
    warn "core patch missing — trying full patch"
    curl -fsSL "${PATCH_FULL_URL}" -o "${tmp}"
  fi
  apply_patch_file "${tmp}"
fi

if ! code_ok; then
  echo "ERROR: CLV fix not in prediction_log.py — check ${APP}/src/hibs_predictor/prediction_log.py" >&2
  exit 1
fi
log "code OK"

touch "${APP}/.env"
for kv in HIBS_CLV_LOG_ENABLED=1 HIBS_CLV_PREDICTED_OUTCOME_FALLBACK=1 HIBS_PREDICTION_LOG_ENABLED=1; do
  k="${kv%%=*}"
  if grep -q "^${k}=" "${APP}/.env" 2>/dev/null; then
    [[ "${k}" == "HIBS_CLV_PREDICTED_OUTCOME_FALLBACK" ]] && \
      sed -i 's/^HIBS_CLV_PREDICTED_OUTCOME_FALLBACK=.*/HIBS_CLV_PREDICTED_OUTCOME_FALLBACK=1/' "${APP}/.env" 2>/dev/null || true
  else
    echo "${kv}" >>"${APP}/.env"
  fi
done
chown www-data:www-data "${APP}/.env" 2>/dev/null || true

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
