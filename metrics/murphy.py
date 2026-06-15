"""Murphy (1973) Brier decomposition: Reliability − Resolution + Uncertainty."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence


def murphy_decomposition(
    forecasts: Sequence[float],
    outcomes: Sequence[int],
    *,
    bins: int = 10,
) -> Dict[str, Any]:
    """Decompose mean binary Brier into reliability, resolution, uncertainty.

    Pool (forecast, outcome) pairs — e.g. every runner leg or every 1X2 leg.
    Returns rounded components; ``brier_score`` should equal ``reliability - resolution + uncertainty``.
    """
    pairs = [(float(f), int(o)) for f, o in zip(forecasts, outcomes)]
    n = len(pairs)
    if n == 0:
        return {
            "n": 0,
            "brier_score": None,
            "reliability": None,
            "resolution": None,
            "uncertainty": None,
        }

    o_bar = sum(o for _, o in pairs) / n
    uncertainty = o_bar * (1.0 - o_bar)

    bucket_n: List[int] = [0] * bins
    bucket_f_sum: List[float] = [0.0] * bins
    bucket_o_sum: List[int] = [0] * bins

    for f, o in pairs:
        f_clamped = min(max(f, 0.0), 1.0)
        idx = min(int(f_clamped * bins), bins - 1)
        bucket_n[idx] += 1
        bucket_f_sum[idx] += f_clamped
        bucket_o_sum[idx] += o

    reliability = 0.0
    resolution = 0.0
    for k in range(bins):
        nk = bucket_n[k]
        if nk == 0:
            continue
        f_bar = bucket_f_sum[k] / nk
        o_hat = bucket_o_sum[k] / nk
        reliability += nk * (f_bar - o_hat) ** 2
        resolution += nk * (o_hat - o_bar) ** 2
    reliability /= n
    resolution /= n

    brier = sum((f - o) ** 2 for f, o in pairs) / n
    check = reliability - resolution + uncertainty
    return {
        "n": n,
        "brier_score": round(brier, 6),
        "reliability": round(reliability, 6),
        "resolution": round(resolution, 6),
        "uncertainty": round(uncertainty, 6),
        "murphy_check": round(check, 6),
    }