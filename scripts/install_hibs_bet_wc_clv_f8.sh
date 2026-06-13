#!/usr/bin/env bash
# Apply WC / inst++ CLV fix (predicted_outcome fallback + scored backfill) on VPS or Mac checkout.
#
#   sudo bash /opt/hibs-bet/scripts/install_wc_clv_f8.sh
#   cd ~/hibs-bet && ./scripts/install_wc_clv_f8.sh
#
# Idempotent — exits 0 if fix already present.
set -euo pipefail

APP="${DEPLOY_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "${APP}"
RAW="${HIBS_BET_RAW:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/hibs-bet/cursor/wc-clv-predicted-outcome-fallback-c4a1}"
RAW_FALLBACK="${HIBS_BET_RAW_FALLBACK:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/patches/hibs-bet}"
PATCH_LOCAL="${APP}/patches/wc-clv-f8.patch"
PY="${APP}/.venv/bin/python3"
[[ -x "${PY}" ]] || PY=python3

log() { echo "[wc-clv-f8] $*"; }
warn() { echo "[wc-clv-f8] WARN: $*" >&2; }

already_applied() {
  grep -q 'clv_predicted_outcome_fallback' "${APP}/src/hibs_predictor/prediction_log.py" 2>/dev/null
}

ensure_env() {
  touch "${APP}/.env"
  for kv in \
    HIBS_CLV_LOG_ENABLED=1 \
    HIBS_CLV_PREDICTED_OUTCOME_FALLBACK=1 \
    HIBS_PREDICTION_LOG_ENABLED=1; do
    k="${kv%%=*}"
    if grep -q "^${k}=" "${APP}/.env" 2>/dev/null; then
      if [[ "${k}" == "HIBS_CLV_PREDICTED_OUTCOME_FALLBACK" ]]; then
        sed -i 's/^HIBS_CLV_PREDICTED_OUTCOME_FALLBACK=.*/HIBS_CLV_PREDICTED_OUTCOME_FALLBACK=1/' "${APP}/.env" 2>/dev/null || true
      fi
    else
      echo "${kv}" >>"${APP}/.env"
    fi
  done
  chown www-data:www-data "${APP}/.env" 2>/dev/null || true
}

apply_patch() {
  local patch_file="$1"
  log "applying ${patch_file}"
  if git -C "${APP}" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "${APP}" am --3way "${patch_file}" || {
      warn "git am failed — trying git apply"
      git -C "${APP}" apply --3way "${patch_file}" || patch -p1 <"${patch_file}"
    }
  else
    patch -p1 <"${patch_file}"
  fi
}

fetch_patch() {
  local dest="$1"
  mkdir -p "$(dirname "${dest}")"
  if [[ -f "${PATCH_LOCAL}" ]]; then
    cp "${PATCH_LOCAL}" "${dest}"
    return 0
  fi
  if curl -fsSL "${RAW}/patches/wc-clv-f8.patch" -o "${dest}" 2>/dev/null; then
    return 0
  fi
  curl -fsSL "${RAW_FALLBACK}/wc-clv-f8.patch" -o "${dest}"
}

run_sync() {
  if [[ -x "${PY}" ]] || command -v python3 >/dev/null 2>&1; then
    log "pred-log-sync (CLV backfill on scored fixtures)"
    export PYTHONPATH="${APP}/src${PYTHONPATH:+:$PYTHONPATH}"
    set -a
    # shellcheck disable=SC1091
    [[ -f "${APP}/.env" ]] && source "${APP}/.env"
    set +a
    "${PY}" -m hibs_predictor.main pred-log-sync --verbose --min-after-kickoff-hours 0 || \
      warn "pred-log-sync failed — rerun after next FT batch"
  fi
}

verify() {
  local n
  n="$(grep -c 'clv_predicted_outcome_fallback' "${APP}/src/hibs_predictor/prediction_log.py" || true)"
  if [[ "${n}" -lt 2 ]]; then
    echo "ERROR: CLV fix not detected in prediction_log.py (grep count=${n})" >&2
    exit 1
  fi
  log "code check OK (grep count=${n})"
  if [[ -f "${APP}/scripts/verify_football_evidence_gates.sh" ]]; then
    log "forward evidence gates"
    bash "${APP}/scripts/verify_football_evidence_gates.sh" || true
  fi
}

main() {
  if already_applied; then
    log "CLV predicted_outcome fallback already installed"
    ensure_env
    run_sync
    verify
    exit 0
  fi

  tmp="$(mktemp)"
  trap 'rm -f "${tmp}"' EXIT
  fetch_patch "${tmp}"
  apply_patch "${tmp}"
  ensure_env
  run_sync
  verify
  log "done — F8 should climb as WC fixtures finish (need n≥25 for buyer_ready)"
}

main "$@"
