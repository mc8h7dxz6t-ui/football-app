"""HTTP/REST backup market tape when WSS is blocked, stale, or shares one API slot."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from hibs_predictor.trading_core.alpaca_websocket import (
    MARKET_TRADE_EVENT,
    STREAM_SOURCE_CLIENT_ORDER_ID,
)
from hibs_predictor.trading_core.coinbase_websocket import (
    coinbase_product_to_trading_symbol,
    trading_symbol_to_coinbase_product,
)
from hibs_predictor.trading_core.historical_market_data import (
    ALPACA_DATA_BASE,
    AlpacaHistoricalFetcher,
    normalize_alpaca_trade_packet,
)
from hibs_predictor.trading_core.metrics import MetricsCollector, StreamLane
from hibs_predictor.trading_core.storage import TradingStorage

logger = logging.getLogger(__name__)

COINBASE_EXCHANGE_API = "https://api.exchange.coinbase.com"


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def rest_market_fallback_enabled() -> bool:
    return _env_truthy("TRADING_REST_MARKET_FALLBACK")


def prefer_rest_market_data() -> bool:
    return _env_truthy("TRADING_PREFER_REST_MARKET_DATA")


def rest_poll_interval_sec() -> float:
    try:
        return max(1.0, float(os.getenv("TRADING_REST_POLL_SEC", "5")))
    except (TypeError, ValueError):
        return 5.0


def rest_stale_trigger_ms() -> float:
    try:
        return max(500.0, float(os.getenv("TRADING_REST_FALLBACK_STALE_MS", "3000")))
    except (TypeError, ValueError):
        return 3000.0


def rest_always_poll() -> bool:
    return _env_truthy("TRADING_REST_MARKET_ALWAYS") or prefer_rest_market_data()


@dataclass(frozen=True)
class RestMarketFallbackConfig:
    equity_symbols: tuple[str, ...]
    crypto_symbols: tuple[str, ...]
    crypto_source: Literal["coinbase", "alpaca"] = "coinbase"
    feed: str = "iex"
    poll_sec: float = 5.0
    stale_trigger_ms: float = 3000.0
    always_poll: bool = False


def normalize_coinbase_rest_trade(product_id: str, trade: dict[str, Any]) -> dict[str, Any] | None:
    try:
        price = float(trade["price"])
        size = float(trade["size"])
    except (KeyError, TypeError, ValueError):
        return None
    if price <= 0 or size <= 0:
        return None
    symbol = coinbase_product_to_trading_symbol(product_id)
    return {
        "T": "t",
        "S": symbol,
        "i": trade.get("trade_id"),
        "p": price,
        "s": int(size) if size == int(size) else size,
        "t": trade.get("time"),
        "source": "coinbase_rest",
        "side": trade.get("side"),
    }


class RestMarketDataClient:
    """Read-only latest-trade HTTP client (Alpaca + Coinbase public REST)."""

    def __init__(
        self,
        *,
        api_key: str = "",
        api_secret: str = "",
        data_base_url: str = ALPACA_DATA_BASE,
        feed: str = "iex",
        timeout_sec: float = 8.0,
    ):
        self._alpaca = AlpacaHistoricalFetcher(
            api_key=api_key,
            api_secret=api_secret,
            data_base_url=data_base_url,
            feed=feed,
        )
        self._timeout = timeout_sec

    def fetch_latest_equity_trades(self, symbols: list[str]) -> list[dict[str, Any]]:
        if not symbols:
            return []
        params = {
            "symbols": ",".join(s.upper() for s in symbols),
            "feed": self._alpaca.feed,
        }
        raw = self._alpaca._get_json("/v2/stocks/trades/latest", params)
        trades_by_symbol = raw.get("trades", {}) if isinstance(raw, dict) else {}
        packets: list[dict[str, Any]] = []
        if isinstance(trades_by_symbol, dict):
            for symbol, trade in trades_by_symbol.items():
                if isinstance(trade, dict):
                    pkt = normalize_alpaca_trade_packet(str(symbol), trade)
                    if pkt:
                        pkt["source"] = "alpaca_rest"
                        packets.append(pkt)
        return packets

    def fetch_latest_crypto_trades_alpaca(self, symbols: list[str]) -> list[dict[str, Any]]:
        if not symbols:
            return []
        params = {"symbols": ",".join(s.upper() for s in symbols)}
        raw = self._alpaca._get_json("/v2/crypto/us/latest/trades", params)
        trades_by_symbol = raw.get("trades", {}) if isinstance(raw, dict) else {}
        packets: list[dict[str, Any]] = []
        if isinstance(trades_by_symbol, dict):
            for symbol, trade in trades_by_symbol.items():
                if isinstance(trade, dict):
                    pkt = normalize_alpaca_trade_packet(str(symbol), trade)
                    if pkt:
                        pkt["source"] = "alpaca_crypto_rest"
                        packets.append(pkt)
        return packets

    def fetch_latest_crypto_trades_coinbase(self, symbols: list[str]) -> list[dict[str, Any]]:
        packets: list[dict[str, Any]] = []
        for symbol in symbols:
            try:
                product = trading_symbol_to_coinbase_product(symbol)
            except ValueError:
                continue
            url = f"{COINBASE_EXCHANGE_API}/products/{urlparse.quote(product)}/trades?limit=1"
            req = urlrequest.Request(url, headers={"User-Agent": "hibs-rest-fallback/1.0"}, method="GET")
            try:
                with urlrequest.urlopen(req, timeout=self._timeout) as response:
                    body = response.read().decode("utf-8")
                    rows = json.loads(body) if body else []
            except urlerror.HTTPError as exc:
                logger.warning("Coinbase REST %s HTTP %s", product, exc.code)
                continue
            except Exception as exc:
                logger.warning("Coinbase REST %s failed: %s", product, exc)
                continue
            if isinstance(rows, list) and rows:
                trade = rows[0]
                if isinstance(trade, dict):
                    pkt = normalize_coinbase_rest_trade(product, trade)
                    if pkt:
                        packets.append(pkt)
        return packets


def ingest_trade_packet(
    *,
    storage: TradingStorage,
    metrics: MetricsCollector,
    packet: dict[str, Any],
    lane: StreamLane,
) -> None:
    """Persist normalized trade packet into broker_events."""
    symbol = str(packet.get("S", "UNKNOWN"))
    trade_id = str(packet.get("i", ""))
    if not trade_id:
        return
    metrics.mark_market_pulse(lane=lane)
    metrics.record_rest_fallback_pulse(lane=lane)
    event_time = packet.get("t")
    storage.append_local_order_event(
        event_type=MARKET_TRADE_EVENT,
        client_order_id=STREAM_SOURCE_CLIENT_ORDER_ID,
        broker_order_id=trade_id,
        payload_json=json.dumps(packet, separators=(",", ":"), sort_keys=True),
        event_time=str(event_time) if event_time is not None else None,
    )
    logger.debug("REST fallback trade lane=%s symbol=%s id=%s", lane, symbol, trade_id)


class RestMarketFallbackLoop:
    """Poll HTTP latest trades when WSS is stale or disabled (shared API slot)."""

    def __init__(
        self,
        *,
        storage: TradingStorage,
        metrics: MetricsCollector,
        client: RestMarketDataClient,
        config: RestMarketFallbackConfig,
    ):
        self.storage = storage
        self.metrics = metrics
        self.client = client
        self.config = config
        self._running = False
        self._seen: dict[str, float] = {}
        self._seen_ttl_sec = 300.0

    def _dedupe(self, lane: str, packet: dict[str, Any]) -> bool:
        trade_id = str(packet.get("i", ""))
        symbol = str(packet.get("S", ""))
        if not trade_id:
            return True
        key = f"{lane}:{symbol}:{trade_id}"
        now = time.monotonic()
        stale = [k for k, t in self._seen.items() if now - t > self._seen_ttl_sec]
        for k in stale:
            del self._seen[k]
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    def _lane_needs_poll(self, lane: StreamLane) -> bool:
        if self.config.always_poll:
            return True
        stale = self.metrics.stale_feed_age_ms(lane)
        if stale == float("inf") or stale > self.config.stale_trigger_ms:
            return True
        return False

    def poll_once(self) -> int:
        ingested = 0
        if self.config.equity_symbols and self._lane_needs_poll("equity"):
            try:
                packets = self.client.fetch_latest_equity_trades(list(self.config.equity_symbols))
            except Exception as exc:
                logger.warning("REST equity poll failed: %s", exc)
                packets = []
            for pkt in packets:
                if self._dedupe("equity", pkt):
                    continue
                ingest_trade_packet(
                    storage=self.storage,
                    metrics=self.metrics,
                    packet=pkt,
                    lane="equity",
                )
                ingested += 1
        if self.config.crypto_symbols and self._lane_needs_poll("crypto"):
            try:
                if self.config.crypto_source == "alpaca":
                    packets = self.client.fetch_latest_crypto_trades_alpaca(
                        list(self.config.crypto_symbols)
                    )
                else:
                    packets = self.client.fetch_latest_crypto_trades_coinbase(
                        list(self.config.crypto_symbols)
                    )
            except Exception as exc:
                logger.warning("REST crypto poll failed: %s", exc)
                packets = []
            for pkt in packets:
                if self._dedupe("crypto", pkt):
                    continue
                ingest_trade_packet(
                    storage=self.storage,
                    metrics=self.metrics,
                    packet=pkt,
                    lane="crypto",
                )
                ingested += 1
        return ingested

    async def run_forever(self) -> None:
        self._running = True
        logger.info(
            "REST market fallback started equity=%s crypto=%s always=%s",
            ",".join(self.config.equity_symbols),
            ",".join(self.config.crypto_symbols),
            self.config.always_poll,
        )
        try:
            while self._running:
                await asyncio.to_thread(self.poll_once)
                await asyncio.sleep(self.config.poll_sec)
        except asyncio.CancelledError:
            raise
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False


def build_rest_fallback_config(
    *,
    equity_symbols: tuple[str, ...],
    crypto_symbols: tuple[str, ...],
    crypto_source: str = "coinbase",
    feed: str = "iex",
) -> RestMarketFallbackConfig | None:
    if not rest_market_fallback_enabled() and not prefer_rest_market_data():
        return None
    if not equity_symbols and not crypto_symbols:
        return None
    source: Literal["coinbase", "alpaca"] = (
        "alpaca" if crypto_source.strip().lower() == "alpaca" else "coinbase"
    )
    return RestMarketFallbackConfig(
        equity_symbols=equity_symbols,
        crypto_symbols=crypto_symbols,
        crypto_source=source,
        feed=feed,
        poll_sec=rest_poll_interval_sec(),
        stale_trigger_ms=rest_stale_trigger_ms(),
        always_poll=rest_always_poll(),
    )
