"""In-play institutional evidence gates I1–I5 + buyer_ready."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from inplay.evidence_store import (
    clv_summary,
    feed_telemetry,
    marks_coverage_summary,
    model_sanity_summary,
    paper_summary,
)

# Institutional thresholds (forward window post deploy)
I1_MAX_SEQ_GAPS = 0
I1_PEEL_P99_MS = 50.0
I1_MIN_FRAMES = 100
I2_MAX_MC_CF_DIFF = 0.03
I2_MIN_SANITY_CHECKS = 10
I3_MIN_MARKS_COVERAGE_PCT = 50.0
I3_MIN_MARK_SNAPSHOTS = 5
I4_MIN_CLV_ROWS = 25
I4_CLV_BEAT_PASS_PCT = 50.0
I5_MIN_PAPER_ROWS = 25


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def evidence_deploy_since_iso() -> Optional[str]:
    explicit = (os.getenv("HIBS_INPLAY_EVIDENCE_DEPLOY_DATE") or "").strip()
    if not explicit:
        explicit = (os.getenv("HIBS_EVIDENCE_DEPLOY_DATE") or "").strip()
    if not explicit:
        return None
    if "T" not in explicit:
        explicit = f"{explicit}T00:00:00+00:00"
    return explicit


def _gate(
    gate_id: str,
    *,
    label: str,
    passed: bool,
    actual: Any,
    threshold: str,
    message: str = "",
    critical: bool = False,
    n: int | None = None,
) -> Dict[str, Any]:
    return {
        "id": gate_id,
        "label": label,
        "pass": passed,
        "actual": actual,
        "threshold": threshold,
        "message": message,
        "critical": critical,
        "n": n,
    }


def inplay_evidence_gates() -> Dict[str, Any]:
    """Evaluate I1–I5 from evidence store (since deploy when set)."""
    since = evidence_deploy_since_iso()
    feed = feed_telemetry(since_iso=since)
    marks = marks_coverage_summary(since_iso=since)
    clv = clv_summary(since_iso=since)
    paper = paper_summary(since_iso=since)
    sanity = model_sanity_summary(since_iso=since)

    peel_limit = _env_float("HIBS_INPLAY_I1_PEEL_P99_MS", I1_PEEL_P99_MS)
    min_frames = int(_env_float("HIBS_INPLAY_I1_MIN_FRAMES", I1_MIN_FRAMES))
    mc_diff_limit = _env_float("HIBS_INPLAY_I2_MAX_DIFF", I2_MAX_MC_CF_DIFF)
    marks_cov_limit = _env_float("HIBS_INPLAY_I3_MIN_COVERAGE_PCT", I3_MIN_MARKS_COVERAGE_PCT)
    clv_min = int(_env_float("HIBS_INPLAY_I4_MIN_ROWS", I4_MIN_CLV_ROWS))
    clv_beat_limit = _env_float("HIBS_INPLAY_I4_BEAT_PCT", I4_CLV_BEAT_PASS_PCT)
    paper_min = int(_env_float("HIBS_INPLAY_I5_MIN_ROWS", I5_MIN_PAPER_ROWS))

    i1_peel_ok = feed["peel_ms_p99"] is None or float(feed["peel_ms_p99"]) <= peel_limit
    i1_seq_ok = int(feed["seq_gaps"]) <= I1_MAX_SEQ_GAPS
    i1_frames_ok = int(feed["n_frames"]) >= min_frames
    i1_pass = i1_peel_ok and i1_seq_ok and i1_frames_ok

    i2_pass = (
        int(sanity["n_checks"]) >= I2_MIN_SANITY_CHECKS
        and sanity["max_abs_diff"] is not None
        and float(sanity["max_abs_diff"]) <= mc_diff_limit
    )

    i3_pass = (
        int(marks["n_snapshots"]) >= I3_MIN_MARK_SNAPSHOTS
        and marks["avg_coverage_pct"] is not None
        and float(marks["avg_coverage_pct"]) >= marks_cov_limit
    )

    i4_n = int(clv["n"] or 0)
    i4_pass = (
        i4_n >= clv_min
        and clv["beat_close_pct"] is not None
        and float(clv["beat_close_pct"]) >= clv_beat_limit
    )

    i5_n = int(paper["with_verification_hash"] or 0)
    i5_pass = i5_n >= paper_min

    gates: List[Dict[str, Any]] = [
        _gate(
            "I1_feed",
            label="Binary feed telemetry",
            passed=i1_pass,
            actual={
                "n_frames": feed["n_frames"],
                "seq_gaps": feed["seq_gaps"],
                "peel_ms_p99": feed["peel_ms_p99"],
                "vendors": feed["vendors"],
            },
            threshold=f"frames>={min_frames}, gaps=0, p99_peel<={peel_limit}ms",
            message="Opta/Sportradar binary ingest + Rust peel",
            critical=True,
            n=feed["n_frames"],
        ),
        _gate(
            "I2_model",
            label="MC vs closed-form sanity",
            passed=i2_pass,
            actual=sanity,
            threshold=f"n>={I2_MIN_SANITY_CHECKS}, max_diff<={mc_diff_limit}",
            message="Held-out tick archive checks",
            critical=False,
            n=sanity["n_checks"],
        ),
        _gate(
            "I3_marks",
            label="Exchange marks coverage",
            passed=i3_pass,
            actual=marks,
            threshold=f"avg_coverage>={marks_cov_limit}%",
            message="Pinnacle/Betfair/Matchbook fetch_exchange_marks",
            critical=False,
            n=marks["n_snapshots"],
        ),
        _gate(
            "I4_clv",
            label="In-play CLV beat-close",
            passed=i4_pass,
            actual=clv,
            threshold=f"n>={clv_min}, beat>={clv_beat_limit}%",
            message="Close-of-window fair vs odds taken",
            critical=False,
            n=i4_n,
        ),
        _gate(
            "I5_paper",
            label="In-play paper ledger",
            passed=i5_pass,
            actual=paper,
            threshold=f"n>={paper_min} with verification_hash",
            message="Paper rows with SHA verification",
            critical=False,
            n=i5_n,
        ),
    ]

    critical = [g for g in gates if g.get("critical")]
    evidence = [g for g in gates if not g.get("critical")]
    critical_pass = all(g["pass"] for g in critical)
    evidence_pass = all(g["pass"] for g in evidence)
    passed_n = sum(1 for g in gates if g["pass"])
    ratio = passed_n / max(len(gates), 1)

    if not critical_pass:
        grade = "D"
    elif evidence_pass:
        grade = "A"
    elif ratio >= 0.85:
        grade = "B+"
    elif ratio >= 0.7:
        grade = "B"
    else:
        grade = "C"

    buyer_ready = critical_pass and evidence_pass

    return {
        "vertical": "inplay",
        "since_deploy_iso": since,
        "gates": gates,
        "critical_pass": critical_pass,
        "evidence_pass": evidence_pass,
        "evidence_grade": grade,
        "buyer_ready": buyer_ready,
        "buyer_readiness_score": round(100.0 * ratio, 1),
        "telemetry": {
            "feed": feed,
            "marks": marks,
            "clv": clv,
            "paper": paper,
            "sanity": sanity,
        },
    }
