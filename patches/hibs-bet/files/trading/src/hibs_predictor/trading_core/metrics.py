"""Low-overhead in-process metrics for trading operations."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
from typing import Literal

StreamLane = Literal["equity", "crypto"]


@dataclass(frozen=True)
class ServiceLevelObjectives:
    """Institutional alert thresholds exported as gauges."""

    tick_to_trade_p99_ms: float = 10.0
    slippage_abs_p99_bps: float = 5.0
    spread_max_single_obs_bps: float = 5.0


@dataclass
class Histogram:
    """Prometheus histogram accumulator with cumulative buckets."""

    buckets: tuple[float, ...]
    bucket_counts: dict[float, int] = field(default_factory=dict)
    count: int = 0
    total: float = 0.0

    def __post_init__(self) -> None:
        if not self.buckets:
            raise ValueError("histogram buckets cannot be empty")
        sorted_buckets = tuple(sorted(self.buckets))
        if sorted_buckets[-1] != math.inf:
            raise ValueError("histogram buckets must include +Inf")
        self.buckets = sorted_buckets
        if not self.bucket_counts:
            self.bucket_counts = {b: 0 for b in self.buckets}

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += float(value)
        for bound in self.buckets:
            if value <= bound:
                self.bucket_counts[bound] += 1

    def percentile_upper_bound(self, quantile: float) -> float:
        """Return the first bucket boundary whose cumulative count crosses quantile."""
        if self.count == 0:
            return 0.0
        target = max(1, math.ceil(self.count * quantile))
        for bound in self.buckets:
            if self.bucket_counts[bound] >= target:
                return bound
        return self.buckets[-1]


@dataclass
class TradingMetrics:
    """Prometheus-friendly counters/gauges (export via scrape helper)."""

    reconciliation_runs: int = 0
    reconciliation_drifts: int = 0
    symbol_halts: int = 0
    healthy_cycles: int = 0
    broker_errors: int = 0
    liveness_probes: int = 0
    boot_convergence_failures: int = 0
    stream_messages_total: int = 0
    stream_messages_equity_total: int = 0
    stream_messages_crypto_total: int = 0
    stream_errors_total: int = 0
    stream_errors_equity_total: int = 0
    stream_errors_crypto_total: int = 0
    rest_fallback_pulses_total: int = 0
    rest_fallback_pulses_equity_total: int = 0
    rest_fallback_pulses_crypto_total: int = 0
    stale_feed_equity_ms: float = 0.0
    stale_feed_crypto_ms: float = 0.0
    strategy_scan_cycles_total: int = 0
    strategy_routed_total: int = 0
    strategy_shadow_would_route_total: int = 0
    strategy_shadow_would_route_equity_total: int = 0
    strategy_shadow_would_route_crypto_total: int = 0
    strategy_no_signal_total: int = 0
    strategy_no_signal_equity_total: int = 0
    strategy_no_signal_crypto_total: int = 0
    strategy_gate_block_total: int = 0
    strategy_risk_reject_total: int = 0
    strategy_execution_failed_total: int = 0
    crypto_feed_source: str = "off"
    stale_feed_ms: float = 0.0
    last_tick_to_trade_ms: float = 0.0
    slippage_bps_last: float = 0.0
    live_spread_bps_last: float = 0.0
    spread_delta_bps_last: float = 0.0
    slo_spread_last_violation: int = 0
    tick_to_trade_hist: Histogram = field(
        default_factory=lambda: Histogram((1.0, 2.0, 5.0, 10.0, 50.0, 100.0, math.inf))
    )
    slippage_bps_hist: Histogram = field(
        default_factory=lambda: Histogram((-10.0, -2.0, 0.0, 2.0, 5.0, 20.0, math.inf))
    )
    slippage_abs_bps_hist: Histogram = field(
        default_factory=lambda: Histogram((0.5, 1.0, 2.0, 5.0, 10.0, 20.0, math.inf))
    )
    slo: ServiceLevelObjectives = field(default_factory=ServiceLevelObjectives)
    # per-symbol halt tracking exported as gauge-like dict
    halted_symbols: dict[str, float] = field(default_factory=dict)

    def as_prometheus_lines(self) -> list[str]:
        lines = [
            "# HELP trading_reconciliation_runs_total Total reconciliation cycles run.",
            "# TYPE trading_reconciliation_runs_total counter",
            f"trading_reconciliation_runs_total {self.reconciliation_runs}",
            "# HELP trading_reconciliation_drifts_total Total actionable reconciliation drifts.",
            "# TYPE trading_reconciliation_drifts_total counter",
            f"trading_reconciliation_drifts_total {self.reconciliation_drifts}",
            "# HELP trading_symbol_halts_total Total symbol/global halts triggered.",
            "# TYPE trading_symbol_halts_total counter",
            f"trading_symbol_halts_total {self.symbol_halts}",
            "# HELP trading_healthy_cycles_total Total clean reconciliation cycles.",
            "# TYPE trading_healthy_cycles_total counter",
            f"trading_healthy_cycles_total {self.healthy_cycles}",
            "# HELP trading_broker_errors_total Total broker communication errors.",
            "# TYPE trading_broker_errors_total counter",
            f"trading_broker_errors_total {self.broker_errors}",
            "# HELP trading_boot_convergence_failures_total Boot barrier convergence failures.",
            "# TYPE trading_boot_convergence_failures_total counter",
            f"trading_boot_convergence_failures_total {self.boot_convergence_failures}",
            "# HELP trading_stream_messages_total Total inbound market stream frames processed.",
            "# TYPE trading_stream_messages_total counter",
            f"trading_stream_messages_total {self.stream_messages_total}",
            "# HELP trading_stream_messages_equity_total Equity lane stream frames processed.",
            "# TYPE trading_stream_messages_equity_total counter",
            f"trading_stream_messages_equity_total {self.stream_messages_equity_total}",
            "# HELP trading_stream_messages_crypto_total Crypto lane stream frames processed.",
            "# TYPE trading_stream_messages_crypto_total counter",
            f"trading_stream_messages_crypto_total {self.stream_messages_crypto_total}",
            "# HELP trading_stream_errors_total Total inbound market stream transport/frame errors.",
            "# TYPE trading_stream_errors_total counter",
            f"trading_stream_errors_total {self.stream_errors_total}",
            "# HELP trading_stream_errors_equity_total Equity lane stream errors.",
            "# TYPE trading_stream_errors_equity_total counter",
            f"trading_stream_errors_equity_total {self.stream_errors_equity_total}",
            "# HELP trading_stream_errors_crypto_total Crypto lane stream errors.",
            "# TYPE trading_stream_errors_crypto_total counter",
            f"trading_stream_errors_crypto_total {self.stream_errors_crypto_total}",
            "# HELP trading_rest_fallback_pulses_total HTTP backup tape ingests (all lanes).",
            "# TYPE trading_rest_fallback_pulses_total counter",
            f"trading_rest_fallback_pulses_total {self.rest_fallback_pulses_total}",
            "# HELP trading_rest_fallback_pulses_equity_total HTTP backup equity tape ingests.",
            "# TYPE trading_rest_fallback_pulses_equity_total counter",
            f"trading_rest_fallback_pulses_equity_total {self.rest_fallback_pulses_equity_total}",
            "# HELP trading_rest_fallback_pulses_crypto_total HTTP backup crypto tape ingests.",
            "# TYPE trading_rest_fallback_pulses_crypto_total counter",
            f"trading_rest_fallback_pulses_crypto_total {self.rest_fallback_pulses_crypto_total}",
            "# HELP trading_strategy_scan_cycles_total Strategy scan cycles completed.",
            "# TYPE trading_strategy_scan_cycles_total counter",
            f"trading_strategy_scan_cycles_total {self.strategy_scan_cycles_total}",
            "# HELP trading_strategy_routed_total Orders dispatched through ExecutionActor (live path).",
            "# TYPE trading_strategy_routed_total counter",
            f"trading_strategy_routed_total {self.strategy_routed_total}",
            "# HELP trading_strategy_shadow_would_route_total Shadow dry-run profiles (no WAL submit).",
            "# TYPE trading_strategy_shadow_would_route_total counter",
            f"trading_strategy_shadow_would_route_total {self.strategy_shadow_would_route_total}",
            "# HELP trading_strategy_shadow_would_route_equity_total Shadow would-route on equity lane.",
            "# TYPE trading_strategy_shadow_would_route_equity_total counter",
            f"trading_strategy_shadow_would_route_equity_total {self.strategy_shadow_would_route_equity_total}",
            "# HELP trading_strategy_shadow_would_route_crypto_total Shadow would-route on crypto lane.",
            "# TYPE trading_strategy_shadow_would_route_crypto_total counter",
            f"trading_strategy_shadow_would_route_crypto_total {self.strategy_shadow_would_route_crypto_total}",
            "# HELP trading_strategy_no_signal_total Strategy scans with no OFI threshold breach.",
            "# TYPE trading_strategy_no_signal_total counter",
            f"trading_strategy_no_signal_total {self.strategy_no_signal_total}",
            "# HELP trading_strategy_no_signal_equity_total NO_SIGNAL on equity lane.",
            "# TYPE trading_strategy_no_signal_equity_total counter",
            f"trading_strategy_no_signal_equity_total {self.strategy_no_signal_equity_total}",
            "# HELP trading_strategy_no_signal_crypto_total NO_SIGNAL on crypto lane.",
            "# TYPE trading_strategy_no_signal_crypto_total counter",
            f"trading_strategy_no_signal_crypto_total {self.strategy_no_signal_crypto_total}",
            "# HELP trading_crypto_feed_source Crypto market data vendor label (coinbase|alpaca|off).",
            "# TYPE trading_crypto_feed_source gauge",
            f'trading_crypto_feed_source{{source="{self.crypto_feed_source}"}} 1',
            "# HELP trading_strategy_gate_block_total Strategy scans blocked by staged gate limits.",
            "# TYPE trading_strategy_gate_block_total counter",
            f"trading_strategy_gate_block_total {self.strategy_gate_block_total}",
            "# HELP trading_strategy_risk_reject_total Strategy scans rejected by RiskKernel.",
            "# TYPE trading_strategy_risk_reject_total counter",
            f"trading_strategy_risk_reject_total {self.strategy_risk_reject_total}",
            "# HELP trading_strategy_execution_failed_total Strategy scans with execution failures.",
            "# TYPE trading_strategy_execution_failed_total counter",
            f"trading_strategy_execution_failed_total {self.strategy_execution_failed_total}",
            "# HELP trading_liveness_probes_total Total /live liveness probe hits.",
            "# TYPE trading_liveness_probes_total counter",
            f"trading_liveness_probes_total {self.liveness_probes}",
            "# HELP trading_process_alive Process liveness indicator (1=alive).",
            "# TYPE trading_process_alive gauge",
            "trading_process_alive 1",
            "# HELP trading_stale_feed_ms Age of latest market pulse in milliseconds (any lane).",
            "# TYPE trading_stale_feed_ms gauge",
            f"trading_stale_feed_ms {self.stale_feed_ms:.3f}",
            "# HELP trading_stale_feed_equity_ms Age of latest equity stream pulse in milliseconds.",
            "# TYPE trading_stale_feed_equity_ms gauge",
            f"trading_stale_feed_equity_ms {self.stale_feed_equity_ms:.3f}",
            "# HELP trading_stale_feed_crypto_ms Age of latest crypto stream pulse in milliseconds.",
            "# TYPE trading_stale_feed_crypto_ms gauge",
            f"trading_stale_feed_crypto_ms {self.stale_feed_crypto_ms:.3f}",
            "# HELP trading_tick_to_trade_ms Latest measured tick-to-trade latency.",
            "# TYPE trading_tick_to_trade_ms gauge",
            f"trading_tick_to_trade_ms {self.last_tick_to_trade_ms:.3f}",
            "# HELP trading_tick_to_trade_ms_hist Tick-to-trade latency distribution.",
            "# TYPE trading_tick_to_trade_ms_hist histogram",
            "# HELP trading_slippage_bps_last Latest observed slippage in basis points.",
            "# TYPE trading_slippage_bps_last gauge",
            f"trading_slippage_bps_last {self.slippage_bps_last:.3f}",
            "# HELP trading_live_spread_bps_last Latest observed live bid-ask spread in bps.",
            "# TYPE trading_live_spread_bps_last gauge",
            f"trading_live_spread_bps_last {self.live_spread_bps_last:.3f}",
            "# HELP trading_spread_delta_bps_last Live spread minus model assumption (bps).",
            "# TYPE trading_spread_delta_bps_last gauge",
            f"trading_spread_delta_bps_last {self.spread_delta_bps_last:.3f}",
            "# HELP trading_slo_spread_last_violation 1 when latest live spread exceeds single-obs SLO.",
            "# TYPE trading_slo_spread_last_violation gauge",
            f"trading_slo_spread_last_violation {self.slo_spread_last_violation}",
            "# HELP trading_slippage_bps_hist Slippage basis points distribution.",
            "# TYPE trading_slippage_bps_hist histogram",
            "# HELP trading_slippage_abs_bps_hist Absolute slippage basis points distribution.",
            "# TYPE trading_slippage_abs_bps_hist histogram",
            "# HELP trading_symbol_halted Symbol halt indicator (1 means halted).",
            "# TYPE trading_symbol_halted gauge",
        ]
        lines.extend(_histogram_lines("trading_tick_to_trade_ms_hist", self.tick_to_trade_hist))
        lines.extend(_histogram_lines("trading_slippage_bps_hist", self.slippage_bps_hist))
        lines.extend(_histogram_lines("trading_slippage_abs_bps_hist", self.slippage_abs_bps_hist))
        lines.extend(_slo_lines(self))
        for symbol in self.halted_symbols:
            safe = symbol.replace("/", "_").replace(":", "_")
            lines.append(f'trading_symbol_halted{{symbol="{safe}"}} 1')
        return lines


class MetricsCollector:
    """Thread-safe enough for single-writer daemon loops."""

    def __init__(self) -> None:
        self.metrics = TradingMetrics()
        self._last_market_pulse = time.monotonic()
        self._lane_pulse: dict[str, float] = {}
        self._required_stream_lanes: tuple[str, ...] = ()

    def set_required_stream_lanes(self, lanes: tuple[str, ...]) -> None:
        self._required_stream_lanes = tuple(lanes)

    def set_crypto_feed_source(self, source: str) -> None:
        self.metrics.crypto_feed_source = source.strip().lower() or "off"

    def mark_market_pulse(self, lane: str | None = None) -> None:
        now = time.monotonic()
        self._last_market_pulse = now
        if lane:
            self._lane_pulse[lane] = now

    def stale_feed_age_ms(self, lane: str | None = None) -> float:
        if lane:
            last = self._lane_pulse.get(lane)
            if last is None:
                return float("inf")
            return (time.monotonic() - last) * 1000.0
        return (time.monotonic() - self._last_market_pulse) * 1000.0

    def refresh_stale_feed_gauges(self) -> None:
        """Update exported stale-feed gauges (call before Prometheus scrape)."""
        self.metrics.stale_feed_ms = self.stale_feed_age_ms()
        self.metrics.stale_feed_equity_ms = self.stale_feed_age_ms("equity")
        self.metrics.stale_feed_crypto_ms = self.stale_feed_age_ms("crypto")

    def record_reconciliation(self, *, drift_count: int) -> None:
        self.metrics.reconciliation_runs += 1
        if drift_count:
            self.metrics.reconciliation_drifts += drift_count

    def record_symbol_halt(self, symbol: str, until_monotonic: float) -> None:
        self.metrics.symbol_halts += 1
        self.metrics.halted_symbols[symbol] = until_monotonic

    def record_healthy_cycle(self) -> None:
        self.metrics.healthy_cycles += 1

    def record_broker_error(self) -> None:
        self.metrics.broker_errors += 1

    def record_liveness_probe(self) -> None:
        """Count /live hits without affecting market-feed freshness signals."""
        self.metrics.liveness_probes += 1

    def record_boot_convergence_failure(self) -> None:
        self.metrics.boot_convergence_failures += 1

    def record_stream_message(self, *, lane: str | None = None) -> None:
        self.metrics.stream_messages_total += 1
        if lane == "equity":
            self.metrics.stream_messages_equity_total += 1
        elif lane == "crypto":
            self.metrics.stream_messages_crypto_total += 1

    def record_stream_error(self, *, lane: str | None = None) -> None:
        self.metrics.stream_errors_total += 1
        if lane == "equity":
            self.metrics.stream_errors_equity_total += 1
        elif lane == "crypto":
            self.metrics.stream_errors_crypto_total += 1

    def record_rest_fallback_pulse(self, *, lane: str | None = None) -> None:
        self.metrics.rest_fallback_pulses_total += 1
        if lane == "equity":
            self.metrics.rest_fallback_pulses_equity_total += 1
        elif lane == "crypto":
            self.metrics.rest_fallback_pulses_crypto_total += 1

    def record_strategy_scan_cycle(
        self,
        *,
        routed: int,
        shadow_would_route: int,
        no_signal: int = 0,
        gate_block: int = 0,
        risk_reject: int = 0,
        execution_failed: int = 0,
        shadow_would_route_equity: int = 0,
        shadow_would_route_crypto: int = 0,
        no_signal_equity: int = 0,
        no_signal_crypto: int = 0,
    ) -> None:
        """Aggregate per-cycle strategy outcomes for Prometheus (audit JSONL remains source of truth)."""
        self.metrics.strategy_scan_cycles_total += 1
        self.metrics.strategy_routed_total += int(routed)
        self.metrics.strategy_shadow_would_route_total += int(shadow_would_route)
        self.metrics.strategy_shadow_would_route_equity_total += int(shadow_would_route_equity)
        self.metrics.strategy_shadow_would_route_crypto_total += int(shadow_would_route_crypto)
        self.metrics.strategy_no_signal_total += int(no_signal)
        self.metrics.strategy_no_signal_equity_total += int(no_signal_equity)
        self.metrics.strategy_no_signal_crypto_total += int(no_signal_crypto)
        self.metrics.strategy_gate_block_total += int(gate_block)
        self.metrics.strategy_risk_reject_total += int(risk_reject)
        self.metrics.strategy_execution_failed_total += int(execution_failed)

    def record_tick_to_trade(self, latency_ms: float) -> None:
        self.metrics.last_tick_to_trade_ms = latency_ms
        self.metrics.tick_to_trade_hist.observe(latency_ms)

    def record_slippage_bps(self, bps: float) -> None:
        self.metrics.slippage_bps_last = bps
        self.metrics.slippage_bps_hist.observe(bps)
        self.metrics.slippage_abs_bps_hist.observe(abs(bps))

    def record_live_spread_bps(self, spread_bps: float) -> None:
        """Record live top-of-book spread; updates abs histogram and last-obs SLO gauge."""
        abs_bps = abs(float(spread_bps))
        self.metrics.live_spread_bps_last = abs_bps
        self.metrics.slippage_abs_bps_hist.observe(abs_bps)
        limit = self.metrics.slo.spread_max_single_obs_bps
        self.metrics.slo_spread_last_violation = 1 if abs_bps > limit else 0

    def record_spread_delta_bps(self, delta_bps: float) -> None:
        self.metrics.spread_delta_bps_last = float(delta_bps)
        self.record_slippage_bps(delta_bps)

    def record_spread_slippage(self, observed_bps: float) -> None:
        """Alias for live spread recording (Harvested Execution shadow soak API)."""
        self.record_live_spread_bps(observed_bps)

    def render_prometheus_exposition(self) -> str:
        """Export current collector state in Prometheus text format."""
        self.metrics.stale_feed_ms = self.stale_feed_age_ms()
        return "\n".join(self.metrics.as_prometheus_lines()) + "\n"


def _histogram_lines(metric_name: str, histogram: Histogram) -> list[str]:
    lines: list[str] = []
    for bound in histogram.buckets:
        le = "+Inf" if math.isinf(bound) else f"{bound:g}"
        count = histogram.bucket_counts[bound]
        lines.append(f'{metric_name}_bucket{{le="{le}"}} {count}')
    lines.append(f"{metric_name}_sum {histogram.total:.6f}")
    lines.append(f"{metric_name}_count {histogram.count}")
    return lines


def _slo_lines(metrics: TradingMetrics) -> list[str]:
    p99_tick = metrics.tick_to_trade_hist.percentile_upper_bound(0.99)
    p99_slippage_abs = metrics.slippage_abs_bps_hist.percentile_upper_bound(0.99)
    tick_violation = 1 if p99_tick > metrics.slo.tick_to_trade_p99_ms else 0
    slippage_violation = 1 if p99_slippage_abs > metrics.slo.slippage_abs_p99_bps else 0
    lines = [
        "# HELP trading_tick_to_trade_p99_upper_bound_ms Estimated p99 upper bound from histogram.",
        "# TYPE trading_tick_to_trade_p99_upper_bound_ms gauge",
        f"trading_tick_to_trade_p99_upper_bound_ms {p99_tick if not math.isinf(p99_tick) else 1e9:.3f}",
        "# HELP trading_slippage_abs_p99_upper_bound_bps Estimated absolute slippage p99 bound.",
        "# TYPE trading_slippage_abs_p99_upper_bound_bps gauge",
        f"trading_slippage_abs_p99_upper_bound_bps {p99_slippage_abs if not math.isinf(p99_slippage_abs) else 1e9:.3f}",
        "# HELP trading_slo_tick_to_trade_violation 1 when p99 tick-to-trade exceeds SLO.",
        "# TYPE trading_slo_tick_to_trade_violation gauge",
        f"trading_slo_tick_to_trade_violation {tick_violation}",
        "# HELP trading_slo_slippage_violation 1 when absolute p99 slippage exceeds SLO.",
        "# TYPE trading_slo_slippage_violation gauge",
        f"trading_slo_slippage_violation {slippage_violation}",
    ]
    return lines
