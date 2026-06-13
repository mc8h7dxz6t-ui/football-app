"""Master lifecycle coordinator for trading core runtime."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import signal
from decimal import Decimal
from typing import Literal

from hibs_predictor.trading_core.engine_event_log import append_engine_event
from hibs_predictor.trading_symbols import is_crypto_symbol, merge_symbol_lists, parse_symbol_list, partition_symbols
from hibs_predictor.trading_core.alpaca_adapter import AlpacaPaperAdapter
from hibs_predictor.trading_core.contracts import OrderIntent
from hibs_predictor.trading_core.alpaca_websocket import BoundedAlpacaStreamConsumer
from hibs_predictor.trading_core.base_broker import BaseBrokerAdapter, OrderResponse, ReplayBrokerAdapter
from hibs_predictor.trading_core.boot_barrier import BootBarrier, BootConvergenceConfig
from hibs_predictor.trading_core.broker_gateway import BrokerExecutionGateway
from hibs_predictor.trading_core.execution_actor import ExecutionActor
from hibs_predictor.trading_core.gate_enforcer import (
    DeploymentPhase,
    GateEnforcerConfig,
    StagedGateEnforcer,
    parse_deployment_phase,
)
from hibs_predictor.trading_core.ledger_store import LedgerStore
from hibs_predictor.trading_core.liquidity_profiler import LiquiditySpreadProfiler
from hibs_predictor.trading_core.metrics import MetricsCollector
from hibs_predictor.trading_core.metrics_daemon import MetricsDaemon, MetricsDaemonConfig
from hibs_predictor.trading_core.reconciliation_engine import ReconciliationEngine
from hibs_predictor.trading_core.reconciliation_service import ReconciliationService, ReconciliationServiceConfig
from hibs_predictor.trading_core.risk_kernel import RiskKernel
from hibs_predictor.trading_core.storage import TradingStorage
from hibs_predictor.trading_core.strategy_engine import (
    AlphaStrategyEngine,
    SignalGateMode,
    StrategyGateConfig,
)
from hibs_predictor.trading_core.strategy_gate_config import (
    load_dual_lane_strategy_config_from_env,
    load_strategy_gate_config_from_env,
)
from hibs_predictor.trading_core.strategy_runner import StrategyRunner, order_intent_to_order_request
from hibs_predictor.trading_core.market_data_streams import (
    build_crypto_stream_consumer,
    crypto_market_stream_enabled_from_env,
    resolve_crypto_market_data_source,
)
from hibs_predictor.trading_core.rest_market_fallback import (
    RestMarketDataClient,
    RestMarketFallbackLoop,
    build_rest_fallback_config,
    prefer_rest_market_data,
)
from hibs_predictor.trading_core.trading_config_validation import (
    crypto_enabled_from_env,
    validate_trading_runtime_config,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrchestratorConfig:
    db_path: str
    broker_mode: Literal["replay", "alpaca"]
    alpaca_key: str
    alpaca_secret: str
    alpaca_stream_crypto_key: str
    alpaca_stream_crypto_secret: str
    crypto_market_data_source: str
    alpaca_base_url: str
    hmac_secret: bytes
    cash_tolerance: Decimal
    qty_tolerance: Decimal
    convergence_window_hours: int
    convergence_max_events: int
    allow_broker_seed: bool
    metrics_host: str
    metrics_port: int
    metrics_allow_ips: tuple[str, ...]
    ready_stale_feed_ms_limit: float
    recon_poll_interval_sec: float
    deployment_phase: DeploymentPhase
    gate_min_paper_days: int
    gate_min_shadow_days: int
    enable_market_stream: bool
    market_stream_feed: str
    market_stream_symbols: tuple[str, ...]
    market_stream_crypto_symbols: tuple[str, ...]
    enable_strategy_runner: bool
    strategy_symbols: tuple[str, ...]
    strategy_gate_mode: SignalGateMode
    strategy_event_driven: bool
    strategy_audit_log_path: str
    strategy_scan_interval_sec: float
    enable_spread_profiling: bool
    spread_slippage_audit_path: str
    assumed_spread_bps: Decimal
    strategy_gate_config: StrategyGateConfig
    strategy_window_ticks: int
    strategy_crypto_gate_config: StrategyGateConfig | None = None
    strategy_crypto_window_ticks: int | None = None


class TradingSystemOrchestrator:
    """Coordinates boot gates, background daemons, and graceful teardown."""

    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.storage: TradingStorage | None = None
        self.ledger: LedgerStore | None = None
        self.broker: BaseBrokerAdapter | None = None
        self.metrics = MetricsCollector()
        self.reconciliation_engine: ReconciliationEngine | None = None
        self.reconciliation_service: ReconciliationService | None = None
        self.boot_barrier: BootBarrier | None = None
        self.execution_actor: ExecutionActor | None = None
        self.gateway: BrokerExecutionGateway | None = None
        self.gate_enforcer: StagedGateEnforcer | None = None
        self.metrics_daemon: MetricsDaemon | None = None
        self.stream_consumer: BoundedAlpacaStreamConsumer | None = None
        self.stream_consumer_crypto: BoundedAlpacaStreamConsumer | None = None
        self.strategy_engine: AlphaStrategyEngine | None = None
        self.strategy_runner: StrategyRunner | None = None
        self.risk_kernel: RiskKernel | None = None
        self._recon_task: asyncio.Task[None] | None = None
        self._stream_task: asyncio.Task[None] | None = None
        self._stream_crypto_task: asyncio.Task[None] | None = None
        self._rest_fallback_task: asyncio.Task[None] | None = None
        self._rest_fallback_loop: RestMarketFallbackLoop | None = None
        self._strategy_task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()

    async def initialize_and_arm(self) -> bool:
        try:
            strategy_crypto_symbols = tuple(
                s for s in self.config.strategy_symbols if is_crypto_symbol(s)
            )
            enable_crypto = bool(strategy_crypto_symbols) or bool(
                self.config.market_stream_crypto_symbols
            )
            crypto_symbols = self.config.market_stream_crypto_symbols or strategy_crypto_symbols
            config_errors = validate_trading_runtime_config(
                enable_crypto=enable_crypto,
                crypto_symbols=crypto_symbols,
                strategy_symbols=self.config.strategy_symbols,
                enable_market_stream=self.config.enable_market_stream,
                market_stream_symbols=self.config.market_stream_symbols,
                market_stream_crypto_symbols=self.config.market_stream_crypto_symbols,
                alpaca_primary_key=self.config.alpaca_key,
                alpaca_primary_secret=self.config.alpaca_secret,
                alpaca_crypto_stream_key=self.config.alpaca_stream_crypto_key,
                alpaca_crypto_stream_secret=self.config.alpaca_stream_crypto_secret,
            )
            if config_errors:
                for msg in config_errors:
                    logger.critical("Trading config invalid: %s", msg)
                return False

            logger.info("Initializing trading core infrastructure")
            Path(self.config.db_path).parent.mkdir(parents=True, exist_ok=True)
            self.storage = TradingStorage(self.config.db_path)
            self.storage.initialize()
            self.ledger = LedgerStore(self.storage.conn)
            self.gate_enforcer = StagedGateEnforcer(
                self.storage.conn,
                config=GateEnforcerConfig(
                    phase=self.config.deployment_phase,
                    min_paper_days=self.config.gate_min_paper_days,
                    min_shadow_days=self.config.gate_min_shadow_days,
                ),
            )

            self.broker = self._build_broker()
            self.reconciliation_engine = ReconciliationEngine(
                cash_tolerance=self.config.cash_tolerance,
                qty_tolerance=self.config.qty_tolerance,
            )
            self.execution_actor = ExecutionActor(
                storage=self.storage,
                secret=self.config.hmac_secret,
                dispatch_order=lambda *_args: None,
            )
            self.boot_barrier = BootBarrier(
                broker=self.broker,
                storage=self.storage,
                ledger=self.ledger,
                reconciliation=self.reconciliation_engine,
                execution_actor=self.execution_actor,
                allow_broker_seed=self.config.allow_broker_seed,
                convergence=BootConvergenceConfig(
                    max_age_seconds=self.config.convergence_window_hours * 3600,
                    max_events=self.config.convergence_max_events,
                ),
            )

            logger.info("Executing mandatory boot barrier")
            boot = await self.boot_barrier.bootstrap()
            if not boot.armed:
                self.metrics.record_boot_convergence_failure()
                logger.critical("Boot barrier blocked startup: %s", boot.message)
                return False

            gate = self.gate_enforcer.pre_arm_check()
            if not gate.allowed:
                self.boot_barrier.is_armed = False
                self.metrics.record_boot_convergence_failure()
                logger.critical("Deployment gate blocked startup: %s", gate.reason)
                return False

            self.reconciliation_service = ReconciliationService(
                broker=self.broker,
                ledger=self.ledger,
                storage=self.storage,
                reconciliation=self.reconciliation_engine,
                metrics=self.metrics,
                config=ReconciliationServiceConfig(
                    poll_interval_sec=self.config.recon_poll_interval_sec,
                    stale_feed_halt_ms=self.config.ready_stale_feed_ms_limit,
                ),
            )
            self.gateway = BrokerExecutionGateway(
                broker=self.broker,
                storage=self.storage,
                ledger=self.ledger,
                boot_barrier=self.boot_barrier,
                reconciliation_service=self.reconciliation_service,
                gate_enforcer=self.gate_enforcer,
                metrics=self.metrics,
            )
            self.execution_actor.set_dispatch_order(self._gateway_dispatch)
            if self.config.enable_spread_profiling:
                profiler = LiquiditySpreadProfiler(
                    quote_provider=self.broker,
                    metrics=self.metrics,
                    assumed_spread_bps=self.config.assumed_spread_bps,
                    audit_log_path=self.config.spread_slippage_audit_path or None,
                    market_data_feed=self.config.market_stream_feed,
                )
                self.execution_actor.set_liquidity_profiler(profiler)

            self.risk_kernel = RiskKernel(secret=self.config.hmac_secret)
            self.strategy_engine = AlphaStrategyEngine(
                self.storage,
                window_ticks=self.config.strategy_window_ticks,
                gate_config=self.config.strategy_gate_config,
                crypto_gate_config=self.config.strategy_crypto_gate_config,
                crypto_window_ticks=self.config.strategy_crypto_window_ticks,
            )
            audit_path = (
                self.config.strategy_audit_log_path
                if self.config.enable_strategy_runner
                else ""
            )
            self.strategy_runner = StrategyRunner(
                strategy=self.strategy_engine,
                risk_kernel=self.risk_kernel,
                execution_actor=self.execution_actor,
                gateway=self.gateway,
                boot_barrier=self.boot_barrier,
                ledger=self.ledger,
                gate_enforcer=self.gate_enforcer,
                reconciliation_service=self.reconciliation_service,
                audit_log_path=audit_path or None,
                synthetic_spread_bps=self.config.assumed_spread_bps,
                metrics=self.metrics,
            )
            if self.config.enable_market_stream:
                if not self.config.alpaca_key or not self.config.alpaca_secret:
                    logger.critical(
                        "Market stream requested but ALPACA_API_KEY/ALPACA_API_SECRET are missing"
                    )
                    return False
                required_lanes: list[str] = []
                use_wss = not prefer_rest_market_data()
                if use_wss and self.config.market_stream_symbols:
                    self.stream_consumer = BoundedAlpacaStreamConsumer(
                        api_key=self.config.alpaca_key,
                        api_secret=self.config.alpaca_secret,
                        storage=self.storage,
                        metrics=self.metrics,
                        feed=self.config.market_stream_feed,
                        stream_lane="equity",
                    )
                    required_lanes.append("equity")
                elif self.config.market_stream_symbols:
                    required_lanes.append("equity")
                    logger.info(
                        "Equity REST backup mode (TRADING_PREFER_REST_MARKET_DATA=1) — WSS off"
                    )
                if use_wss and self.config.market_stream_crypto_symbols:
                    crypto_key = self.config.alpaca_stream_crypto_key or self.config.alpaca_key
                    crypto_secret = (
                        self.config.alpaca_stream_crypto_secret or self.config.alpaca_secret
                    )
                    self.stream_consumer_crypto = build_crypto_stream_consumer(
                        source=self.config.crypto_market_data_source,  # type: ignore[arg-type]
                        storage=self.storage,
                        metrics=self.metrics,
                        alpaca_key=crypto_key,
                        alpaca_secret=crypto_secret,
                    )
                    required_lanes.append("crypto")
                elif self.config.market_stream_crypto_symbols:
                    required_lanes.append("crypto")
                    logger.info(
                        "Crypto REST backup mode (TRADING_PREFER_REST_MARKET_DATA=1) — WSS off"
                    )
                if required_lanes:
                    self.metrics.set_required_stream_lanes(tuple(required_lanes))
                self.metrics.set_crypto_feed_source(
                    self.config.crypto_market_data_source
                    if self.stream_consumer_crypto
                    else "off"
                )
                if strategy_crypto_symbols and self.stream_consumer_crypto is None:
                    logger.warning(
                        "Crypto strategy symbols=%s but crypto market stream is off "
                        "(set TRADING_CRYPTO_MARKET_STREAM=1 or TRADING_ENABLE_CRYPTO=1)",
                        ",".join(strategy_crypto_symbols),
                    )
                crypto_feed = (
                    self.config.crypto_market_data_source
                    if self.stream_consumer_crypto
                    else "off"
                )
                logger.info(
                    "Market streams configured equity=%s crypto=%s equity_feed=%s crypto_feed=%s wss=%s",
                    ",".join(self.config.market_stream_symbols),
                    ",".join(self.config.market_stream_crypto_symbols),
                    self.config.market_stream_feed,
                    crypto_feed,
                    use_wss,
                )
                rest_cfg = build_rest_fallback_config(
                    equity_symbols=self.config.market_stream_symbols,
                    crypto_symbols=self.config.market_stream_crypto_symbols,
                    crypto_source=self.config.crypto_market_data_source,
                    feed=self.config.market_stream_feed,
                )
                if rest_cfg is not None and self.storage is not None:
                    self._rest_fallback_loop = RestMarketFallbackLoop(
                        storage=self.storage,
                        metrics=self.metrics,
                        client=RestMarketDataClient(
                            api_key=self.config.alpaca_key,
                            api_secret=self.config.alpaca_secret,
                            feed=self.config.market_stream_feed,
                        ),
                        config=rest_cfg,
                    )
                    logger.info(
                        "REST market fallback armed poll_sec=%.1f always=%s",
                        rest_cfg.poll_sec,
                        rest_cfg.always_poll,
                    )
            stream_lanes = tuple(
                lane
                for lane, enabled in (
                    ("equity", self.stream_consumer is not None),
                    ("crypto", self.stream_consumer_crypto is not None),
                )
                if enabled
            )
            self.metrics_daemon = MetricsDaemon(
                collector=self.metrics,
                boot_barrier=self.boot_barrier,
                reconciliation_service=self.reconciliation_service,
                config=MetricsDaemonConfig(
                    host=self.config.metrics_host,
                    port=self.config.metrics_port,
                    allowed_ips=self.config.metrics_allow_ips,
                    ready_stale_feed_ms_limit=self.config.ready_stale_feed_ms_limit,
                    required_stream_lanes=stream_lanes,
                    skip_stale_feed_ready_check=not self.config.enable_market_stream,
                ),
            )
            logger.info("Boot barrier cleared; runtime components wired")
            return True
        except Exception:
            logger.exception("Orchestrator initialization failed")
            return False

    async def run(self) -> None:
        if self.reconciliation_service is None or self.metrics_daemon is None:
            raise RuntimeError("orchestrator not initialized")

        logger.info("Starting reconciliation daemon and metrics server")
        self._recon_task = asyncio.create_task(self.reconciliation_service.run_forever())
        await self.metrics_daemon.start()

        if self.stream_consumer is not None:
            logger.info("Starting equity market stream (feed=%s)", self.config.market_stream_feed)
            self._stream_task = asyncio.create_task(
                self._supervise_market_stream(
                    lane="equity",
                    consumer=self.stream_consumer,
                    symbols=self.config.market_stream_symbols,
                )
            )
        if self.stream_consumer_crypto is not None:
            logger.info("Starting crypto market stream")
            self._stream_crypto_task = asyncio.create_task(
                self._supervise_market_stream(
                    lane="crypto",
                    consumer=self.stream_consumer_crypto,
                    symbols=self.config.market_stream_crypto_symbols,
                )
            )

        if self._rest_fallback_loop is not None:
            logger.info("Starting REST market fallback poller")
            self._rest_fallback_task = asyncio.create_task(self._rest_fallback_loop.run_forever())

        if self.config.enable_strategy_runner and self.strategy_runner is not None:
            if self.config.strategy_event_driven:
                self._strategy_task = asyncio.create_task(self._strategy_event_driven_loop())
            else:
                self._strategy_task = asyncio.create_task(self._strategy_interval_loop())

        engine_log = os.getenv("TRADING_ENGINE_AUDIT", "").strip()
        if engine_log:
            equity = [s for s in self.config.strategy_symbols if not is_crypto_symbol(s)]
            crypto = [s for s in self.config.strategy_symbols if is_crypto_symbol(s)]
            append_engine_event(
                engine_log,
                "ENGINE_ONLINE",
                payload={
                    "phase": self.config.deployment_phase.value,
                    "stream_equity_symbols": list(self.config.market_stream_symbols),
                    "stream_crypto_symbols": list(self.config.market_stream_crypto_symbols),
                    "crypto_market_data_source": self.config.crypto_market_data_source,
                    "strategy_symbols": list(self.config.strategy_symbols),
                    "equity_symbols": equity,
                    "crypto_symbols": crypto,
                    "strategy_audit": self.config.strategy_audit_log_path,
                    "spread_audit": self.config.spread_slippage_audit_path,
                    "metrics_port": self.config.metrics_port,
                },
            )
        logger.info("Trading system online; awaiting shutdown signal")
        await self._shutdown_event.wait()

    async def graceful_shutdown(self) -> None:
        logger.info("Initiating graceful shutdown")

        if self._rest_fallback_task is not None:
            if self._rest_fallback_loop is not None:
                self._rest_fallback_loop.stop()
            self._rest_fallback_task.cancel()
            try:
                await self._rest_fallback_task
            except asyncio.CancelledError:
                pass
            self._rest_fallback_task = None
            self._rest_fallback_loop = None

        if self._stream_task is not None:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
            self._stream_task = None
        if self._stream_crypto_task is not None:
            self._stream_crypto_task.cancel()
            try:
                await self._stream_crypto_task
            except asyncio.CancelledError:
                pass
            self._stream_crypto_task = None
        if self.stream_consumer_crypto is not None:
            await self.stream_consumer_crypto.stop()
            self.stream_consumer_crypto = None
        if self.stream_consumer is not None:
            await self.stream_consumer.stop()
            self.stream_consumer = None

        if self._strategy_task is not None:
            self._strategy_task.cancel()
            try:
                await self._strategy_task
            except asyncio.CancelledError:
                pass
            self._strategy_task = None

        if self.reconciliation_service is not None:
            self.reconciliation_service.stop()
        if self._recon_task is not None:
            self._recon_task.cancel()
            try:
                await self._recon_task
            except asyncio.CancelledError:
                pass
            self._recon_task = None

        if self.metrics_daemon is not None:
            await self.metrics_daemon.stop()

        if self.storage is not None:
            self.storage.close()
            self.storage = None

        logger.info("Trading system offline")

    def request_shutdown(self, reason: str) -> None:
        logger.warning("Shutdown requested: %s", reason)
        self._shutdown_event.set()

    async def _supervise_market_stream(
        self,
        *,
        lane: str,
        consumer: BoundedAlpacaStreamConsumer,
        symbols: tuple[str, ...],
    ) -> None:
        """Reconnect bounded stream lanes with exponential backoff (ingestion-only)."""
        backoff_sec = 1.0
        max_backoff_sec = 60.0
        engine_log = os.getenv("TRADING_ENGINE_AUDIT", "").strip()
        while not self._shutdown_event.is_set():
            try:
                await consumer.connect_and_authenticate()
                await consumer.subscribe_trades(set(symbols))
                backoff_sec = 1.0
                await consumer.start_ingestion_loop()
                if self._shutdown_event.is_set():
                    break
                logger.warning("Market stream lane=%s disconnected; reconnecting", lane)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.metrics.record_stream_error(lane=lane)
                logger.exception("Market stream lane=%s failed: %s", lane, exc)
                if engine_log:
                    append_engine_event(
                        engine_log,
                        "STREAM_LANE_ERROR",
                        payload={"lane": lane, "error": str(exc)},
                    )
            finally:
                try:
                    await consumer.stop()
                except Exception:
                    logger.debug("Stream consumer stop after lane=%s error", lane, exc_info=True)
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=backoff_sec)
                break
            except asyncio.TimeoutError:
                pass
            backoff_sec = min(backoff_sec * 2.0, max_backoff_sec)

    async def _gateway_dispatch(self, intent: OrderIntent, client_order_id: str) -> OrderResponse:
        if self.gateway is None:
            raise RuntimeError("gateway not initialized")
        request = order_intent_to_order_request(intent, client_order_id)
        return await self.gateway.submit_order(request)

    async def _strategy_event_driven_loop(self) -> None:
        """Run strategy scans only after a clean reconciliation cycle (fresh ledger)."""
        if self.strategy_runner is None or self.reconciliation_service is None:
            return
        symbols = list(self.config.strategy_symbols)
        gate_mode = self.config.strategy_gate_mode
        try:
            while not self._shutdown_event.is_set():
                clean_ready = await self.reconciliation_service.wait_for_clean_cycle(
                    shutdown_event=self._shutdown_event,
                )
                if not clean_ready or self._shutdown_event.is_set():
                    break
                if not self.reconciliation_service.state.last_result_clean:
                    continue
                result = await self.strategy_runner.run_scan_cycle(
                    symbols,
                    gate_mode=gate_mode,
                )
                logger.debug(
                    "Strategy event scan cycle_id=%s routed=%s evaluated=%s",
                    result.cycle_id,
                    result.routed,
                    result.evaluated_symbols,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Event-driven strategy scan loop failed")

    async def _strategy_interval_loop(self) -> None:
        """Legacy fixed-interval scan (discouraged; races stale ledger without recon tie-in)."""
        if self.strategy_runner is None:
            return
        interval = max(0.1, float(self.config.strategy_scan_interval_sec))
        symbols = list(self.config.strategy_symbols)
        gate_mode = self.config.strategy_gate_mode
        try:
            while not self._shutdown_event.is_set():
                await self.strategy_runner.run_scan_cycle(
                    symbols,
                    gate_mode=gate_mode,
                )
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=interval)
                    break
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Interval strategy scan loop failed")

    def _build_broker(self) -> BaseBrokerAdapter:
        if self.config.broker_mode == "replay":
            return ReplayBrokerAdapter(venue="REPLAY")
        return AlpacaPaperAdapter(
            api_key=self.config.alpaca_key,
            api_secret=self.config.alpaca_secret,
            base_url=self.config.alpaca_base_url,
        )


def _crypto_symbols_from_env() -> tuple[str, ...]:
    flag = os.getenv("TRADING_ENABLE_CRYPTO", "").strip().lower()
    if flag not in ("1", "true", "yes"):
        return ()
    return parse_symbol_list(os.getenv("STRATEGY_CRYPTO_SYMBOLS", ""))


def _resolve_stream_symbols(args: argparse.Namespace) -> tuple[str, ...]:
    """Equity-only symbols for stock market stream (IEX/SIP)."""
    return parse_symbol_list(str(args.stream_symbols))


def _resolve_strategy_symbols(args: argparse.Namespace) -> tuple[str, ...]:
    extra = ",".join(_crypto_symbols_from_env())
    return merge_symbol_lists(str(args.strategy_symbols), extra)


def build_config_from_args(args: argparse.Namespace) -> OrchestratorConfig:
    secret = os.getenv("TRADING_HMAC_SECRET", "dev-only-hmac-secret-change-me").encode("utf-8")
    if bool(args.enable_strategy_runner):
        try:
            dual_lane = load_dual_lane_strategy_config_from_env()
            strategy_gate_config = dual_lane.equity
            strategy_window_ticks = dual_lane.equity_window_ticks
            strategy_crypto_gate_config = dual_lane.crypto
            strategy_crypto_window_ticks = dual_lane.crypto_window_ticks
        except ValueError as exc:
            raise SystemExit(f"Invalid strategy OFI configuration: {exc}") from exc
    else:
        strategy_gate_config = StrategyGateConfig()
        strategy_window_ticks = 50
        strategy_crypto_gate_config = None
        strategy_crypto_window_ticks = None
    enable_crypto = crypto_enabled_from_env()
    crypto_symbols = _crypto_symbols_from_env()
    market_stream_symbols = _resolve_stream_symbols(args)
    strategy_symbols = _resolve_strategy_symbols(args)
    alpaca_key = args.alpaca_key or os.getenv("ALPACA_API_KEY", "")
    alpaca_secret = args.alpaca_secret or os.getenv("ALPACA_API_SECRET", "")
    alpaca_crypto_key = os.getenv("ALPACA_CRYPTO_API_KEY", "")
    alpaca_crypto_secret = os.getenv("ALPACA_CRYPTO_API_SECRET", "")
    crypto_data_source = resolve_crypto_market_data_source(
        alpaca_primary_key=alpaca_key,
        alpaca_crypto_stream_key=alpaca_crypto_key,
    )
    crypto_stream_on = crypto_market_stream_enabled_from_env(
        enable_crypto=enable_crypto,
        crypto_symbols=crypto_symbols,
        alpaca_primary_key=alpaca_key,
        alpaca_crypto_stream_key=alpaca_crypto_key,
    )
    market_stream_crypto_symbols = crypto_symbols if crypto_stream_on else ()
    config_errors = validate_trading_runtime_config(
        enable_crypto=enable_crypto,
        crypto_symbols=crypto_symbols,
        strategy_symbols=strategy_symbols,
        enable_market_stream=bool(args.enable_market_stream),
        market_stream_symbols=market_stream_symbols,
        market_stream_crypto_symbols=market_stream_crypto_symbols,
        alpaca_primary_key=alpaca_key,
        alpaca_primary_secret=alpaca_secret,
        alpaca_crypto_stream_key=alpaca_crypto_key,
        alpaca_crypto_stream_secret=alpaca_crypto_secret,
    )
    if config_errors:
        raise SystemExit("Trading configuration invalid:\n  - " + "\n  - ".join(config_errors))
    return OrchestratorConfig(
        db_path=args.db_path,
        broker_mode=args.broker,
        alpaca_key=alpaca_key,
        alpaca_secret=alpaca_secret,
        alpaca_stream_crypto_key=alpaca_crypto_key,
        alpaca_stream_crypto_secret=alpaca_crypto_secret,
        crypto_market_data_source=crypto_data_source,
        alpaca_base_url=args.alpaca_url,
        hmac_secret=secret,
        cash_tolerance=Decimal(args.cash_tolerance),
        qty_tolerance=Decimal(args.qty_tolerance),
        convergence_window_hours=args.convergence_window_hours,
        convergence_max_events=args.convergence_max_events,
        allow_broker_seed=bool(args.allow_broker_seed),
        metrics_host=args.metrics_host,
        metrics_port=args.metrics_port,
        metrics_allow_ips=tuple(args.metrics_allow_ip),
        ready_stale_feed_ms_limit=float(args.ready_stale_feed_ms),
        recon_poll_interval_sec=float(args.recon_poll_interval_sec),
        deployment_phase=parse_deployment_phase(
            args.deployment_phase or os.getenv("TRADING_DEPLOYMENT_PHASE", "paper")
        ),
        gate_min_paper_days=int(args.gate_min_paper_days),
        gate_min_shadow_days=int(args.gate_min_shadow_days),
        enable_market_stream=bool(args.enable_market_stream),
        market_stream_feed=str(args.market_stream_feed),
        market_stream_symbols=market_stream_symbols,
        market_stream_crypto_symbols=market_stream_crypto_symbols,
        enable_strategy_runner=bool(args.enable_strategy_runner),
        strategy_symbols=strategy_symbols,
        strategy_gate_mode=_strategy_gate_mode_from_env(args),
        strategy_event_driven=not bool(args.strategy_interval_mode),
        strategy_audit_log_path=str(args.strategy_audit_log),
        strategy_scan_interval_sec=float(args.strategy_scan_interval_sec),
        enable_spread_profiling=not bool(args.disable_spread_profiling),
        spread_slippage_audit_path=str(args.spread_slippage_audit),
        assumed_spread_bps=Decimal(str(args.assumed_spread_bps)),
        strategy_gate_config=strategy_gate_config,
        strategy_window_ticks=strategy_window_ticks,
        strategy_crypto_gate_config=strategy_crypto_gate_config,
        strategy_crypto_window_ticks=strategy_crypto_window_ticks,
    )


def _parse_signal_gate_mode(raw: str) -> SignalGateMode:
    value = raw.strip().upper()
    if value in {mode.value for mode in SignalGateMode}:
        return SignalGateMode(value)
    return SignalGateMode.GATE_1


def _strategy_gate_mode_from_env(args: argparse.Namespace) -> SignalGateMode:
    env_raw = os.getenv("STRATEGY_GATE_MODE", "").strip()
    if env_raw:
        return _parse_signal_gate_mode(env_raw)
    return _parse_signal_gate_mode(str(args.strategy_gate_mode))


async def run_orchestrator(config: OrchestratorConfig) -> int:
    orchestrator = TradingSystemOrchestrator(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: orchestrator.request_shutdown(s.name))
        except NotImplementedError:
            pass

    if not await orchestrator.initialize_and_arm():
        await orchestrator.graceful_shutdown()
        return 1

    try:
        await orchestrator.run()
    finally:
        await orchestrator.graceful_shutdown()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run trading core master orchestrator.")
    parser.add_argument("--db-path", default="data/trading_production.db")
    parser.add_argument("--broker", choices=("replay", "alpaca"), default="replay")
    parser.add_argument("--alpaca-key", default="")
    parser.add_argument("--alpaca-secret", default="")
    parser.add_argument("--alpaca-url", default=os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"))
    parser.add_argument("--cash-tolerance", default="0.01")
    parser.add_argument("--qty-tolerance", default="0.000001")
    parser.add_argument("--convergence-window-hours", type=int, default=24)
    parser.add_argument("--convergence-max-events", type=int, default=10_000)
    parser.add_argument("--allow-broker-seed", action="store_true")
    parser.add_argument("--metrics-host", default="127.0.0.1")
    parser.add_argument("--metrics-port", type=int, default=9108)
    parser.add_argument("--metrics-allow-ip", action="append", default=["127.0.0.1", "::1"])
    parser.add_argument("--ready-stale-feed-ms", type=float, default=5000.0)
    parser.add_argument("--recon-poll-interval-sec", type=float, default=1.0)
    parser.add_argument(
        "--deployment-phase",
        choices=tuple(p.value for p in DeploymentPhase),
        default=None,
        help="Staged rollout phase (paper, shadow, micro, live).",
    )
    parser.add_argument("--gate-min-paper-days", type=int, default=14)
    parser.add_argument("--gate-min-shadow-days", type=int, default=7)
    parser.add_argument(
        "--enable-market-stream",
        action="store_true",
        help="Start bounded Alpaca market-data ingestion (no order routing).",
    )
    parser.add_argument("--market-stream-feed", default="iex", help="Alpaca stream feed (e.g. iex, sip).")
    parser.add_argument(
        "--stream-symbols",
        default=os.getenv("STRATEGY_SYMBOLS", "AAPL,TSLA"),
        help="Comma-separated symbols for trade stream subscription.",
    )
    parser.add_argument(
        "--enable-strategy-runner",
        action="store_true",
        help="Run periodic strategy scan loop (still subject to risk and staged gates).",
    )
    parser.add_argument(
        "--strategy-symbols",
        default=os.getenv("STRATEGY_SYMBOLS", "AAPL,TSLA"),
    )
    parser.add_argument(
        "--strategy-gate-mode",
        default="GATE_1",
        choices=tuple(mode.value for mode in SignalGateMode),
    )
    parser.add_argument(
        "--strategy-interval-mode",
        action="store_true",
        help="Use fixed-interval strategy scans instead of recon-clean-cycle triggers.",
    )
    parser.add_argument(
        "--strategy-audit-log",
        default=os.getenv("TRADING_STRATEGY_AUDIT", "data/strategy_scan_audit.jsonl"),
        help="JSONL path for per-symbol strategy scan decisions.",
    )
    parser.add_argument("--strategy-scan-interval-sec", type=float, default=5.0)
    parser.add_argument(
        "--disable-spread-profiling",
        action="store_true",
        help="Disable execution-layer live spread profiling and JSONL audit.",
    )
    parser.add_argument(
        "--spread-slippage-audit",
        default=os.getenv("TRADING_SPREAD_AUDIT", "data/spread_slippage_audit.jsonl"),
        help="JSONL path for live spread vs model assumption at execution.",
    )
    parser.add_argument(
        "--assumed-spread-bps",
        default="10",
        help="Backtest/risk synthetic half-spread assumption in basis points.",
    )
    return parser.parse_args(argv)
