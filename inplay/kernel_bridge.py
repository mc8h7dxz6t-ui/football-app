"""Optional Rust kernel bridge — Python fallback in feeds_binary."""

from __future__ import annotations

from typing import Any, Dict, Tuple


def peel_frame_rust(vendor: str, payload: bytes) -> Tuple[Dict[str, Any], bytes]:
    """Call Rust extension when built (scripts/build_inplay_kernel.sh)."""
    try:
        import hibs_inplay_kernel  # type: ignore[import-not-found]

        return hibs_inplay_kernel.peel_frame(vendor, payload)
    except ImportError as exc:
        raise RuntimeError("rust kernel not built") from exc


def mc_intensity(home_lambda: float, away_lambda: float, *, paths: int = 100_000) -> Dict[str, float]:
    try:
        import hibs_inplay_kernel  # type: ignore[import-not-found]

        return hibs_inplay_kernel.monte_carlo_1x2(home_lambda, away_lambda, paths)
    except ImportError:
        from inplay.monte_carlo_gpu import simulate_1x2

        return simulate_1x2(home_lambda, away_lambda, paths=paths)
