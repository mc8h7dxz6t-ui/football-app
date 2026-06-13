#!/usr/bin/env bash
# Enable HTTP backup tape for trading (equity Alpaca REST + crypto Coinbase REST).
#
# Use when Alpaca WSS slot is shared (Mac+VPS) or VPS blocks websocket:
#   TRADING_PREFER_REST_MARKET_DATA=1  — WSS off, HTTP only
#   TRADING_REST_MARKET_FALLBACK=1     — WSS on + HTTP when stale (default companion)
#
#   sudo bash /opt/hibs-bet/deploy/apply-trading-rest-fallback.sh
#   sudo bash /opt/hibs-bet/deploy/apply-trading-rest-fallback.sh --prefer-rest
set -euo pipefail

SECRETS="${TRADING_SECRETS_FILE:-/etc/trading_secrets}"
PREFER_REST=0
[[ "${1:-}" == "--prefer-rest" ]] && PREFER_REST=1

log() { echo "[trading-rest] $*"; }

[[ -f "${SECRETS}" ]] || touch "${SECRETS}"
chmod 600 "${SECRETS}" 2>/dev/null || true

upsert() {
  local k="$1" v="$2"
  if grep -q "^${k}=" "${SECRETS}" 2>/dev/null; then
    sed -i "s|^${k}=.*|${k}=${v}|" "${SECRETS}"
  else
    echo "${k}=${v}" >>"${SECRETS}"
  fi
}

upsert TRADING_REST_MARKET_FALLBACK 1
upsert TRADING_REST_POLL_SEC 5
upsert TRADING_REST_FALLBACK_STALE_MS 3000
if [[ "${PREFER_REST}" -eq 1 ]]; then
  upsert TRADING_PREFER_REST_MARKET_DATA 1
  log "WSS disabled — HTTP latest-trades only (frees Alpaca stream slot)"
else
  log "REST supplements WSS when stale (keeps stream when healthy)"
fi

if systemctl is-enabled trading-shadow-soak &>/dev/null; then
  log "restart trading-shadow-soak"
  systemctl restart trading-shadow-soak
  sleep 12
  curl -s http://127.0.0.1:9108/ready || true
  echo ""
fi

log "done — check: curl -s http://127.0.0.1:9108/metrics | grep rest_fallback"
