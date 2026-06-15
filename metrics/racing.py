"""Racing verification — win vs place targets, venue mapping, market benchmark."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Sequence

from metrics.brier import brier_race
from metrics.calibration import calibration_table_all_legs
from metrics.data_room import (
    SCHEMA_VERSION,
    _verdict,
    institutional_gates,
    evaluate_multiclass,
)
from metrics.murphy import murphy_decomposition

TargetKind = Literal["win", "place"]


@dataclass
class RacingRunner:
    runner_id: str
    model_prob: float
    market_prob: Optional[float] = None
    won: bool = False
    placed: bool = False


@dataclass
class RacingRecord:
    race_id: str
    target: TargetKind
    runners: List[RacingRunner]
    venue_id: str = ""
    venue_mapped: bool = True
    place_positions: int = 3

    def outcome_vector(self) -> List[int]:
        if self.target == "win":
            return [1 if r.won else 0 for r in self.runners]
        return [1 if r.placed else 0 for r in self.runners]

    def model_probs(self) -> List[float]:
        return [r.model_prob for r in self.runners]

    def market_probs(self) -> Optional[List[float]]:
        if any(r.market_prob is None for r in self.runners):
            return None
        return [float(r.market_prob) for r in self.runners]  # type: ignore[arg-type]


def racing_record_from_dict(raw: Dict[str, Any], *, validate: bool = True) -> RacingRecord:
    if validate:
        from metrics.racing_validate import validate_race_record

        ok, errs = validate_race_record(raw)
        if not ok:
            raise ValueError("; ".join(errs))
    target = str(raw.get("target", "place")).lower()
    if target not in ("win", "place"):
        raise ValueError(f"target must be win or place, got {target!r}")
    runners = []
    for r in raw.get("runners") or []:
        runners.append(
            RacingRunner(
                runner_id=str(r.get("runner_id", "")),
                model_prob=float(r.get("model_prob", 0.0)),
                market_prob=float(r["market_prob"]) if r.get("market_prob") is not None else None,
                won=bool(r.get("won", False)),
                placed=bool(r.get("placed", False)),
            )
        )
    return RacingRecord(
        race_id=str(raw.get("race_id", "")),
        target=target,  # type: ignore[arg-type]
        runners=runners,
        venue_id=str(raw.get("venue_id", "")),
        venue_mapped=bool(raw.get("venue_mapped", True)),
        place_positions=int(raw.get("place_positions", 3)),
    )


def venue_mapping_summary(races: Sequence[RacingRecord]) -> Dict[str, Any]:
    n = len(races)
    n_mapped = sum(1 for r in races if r.venue_mapped)
    unmapped_venues = sorted({r.venue_id for r in races if not r.venue_mapped and r.venue_id})
    return {
        "n_races": n,
        "n_mapped": n_mapped,
        "n_unmapped": n - n_mapped,
        "mapped_pct": round(n_mapped / n, 4) if n else None,
        "unmapped_venues_sample": unmapped_venues[:20],
        "gate_min_mapped_pct": 0.95,
    }


def _runner_leg_calibration_records(
    races: Sequence[RacingRecord],
    *,
    use_market: bool = False,
) -> List[Dict[str, Any]]:
    """Binary hit/miss records per runner for calibration + Murphy pooling."""
    records: List[Dict[str, Any]] = []
    for race in races:
        for r in race.runners:
            p = r.market_prob if use_market and r.market_prob is not None else r.model_prob
            p = min(max(float(p), 0.0), 1.0)
            hit = r.won if race.target == "win" else r.placed
            records.append(
                {
                    "probs": {"hit": p, "miss": 1.0 - p},
                    "outcome": "hit" if hit else "miss",
                }
            )
    return records


def _macro_brier_per_race(
    races: Sequence[RacingRecord],
    *,
    use_market: bool = False,
) -> Optional[float]:
    scores: List[float] = []
    for race in races:
        probs = race.market_probs() if use_market else race.model_probs()
        if use_market and probs is None:
            continue
        bs = brier_race(probs or [], race.outcome_vector())
        if bs is not None:
            scores.append(bs)
    return sum(scores) / len(scores) if scores else None


def evaluate_racing_window(
    races: Sequence[RacingRecord],
    *,
    min_races: int = 1000,
    bins: int = 10,
    oos_only: bool = True,
    oos_declared: bool = True,
    train_cutoff: Optional[str] = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Institutional racing window — win or place target, venue mapping, market benchmark."""
    from datetime import datetime, timezone

    if not races:
        return {"error": "no_races", "n_races": 0}

    target = races[0].target
    if any(r.target != target for r in races):
        return {"error": "mixed_targets", "n_races": len(races)}

    n_races = len(races)
    venue = venue_mapping_summary(races)
    macro_model = _macro_brier_per_race(races, use_market=False)
    macro_market = _macro_brier_per_race(races, use_market=True)
    delta = (
        round(macro_model - macro_market, 4)
        if macro_model is not None and macro_market is not None
        else None
    )

    model_legs = _runner_leg_calibration_records(races, use_market=False)
    market_legs = _runner_leg_calibration_records(
        [r for r in races if r.market_probs() is not None], use_market=True
    )
    leg_keys = ("hit", "miss")

    model_eval = evaluate_multiclass(model_legs, keys=leg_keys, bins=bins)
    market_eval = evaluate_multiclass(market_legs, keys=leg_keys, bins=bins) if market_legs else {"n": 0}

    gates = institutional_gates(
        n_events=n_races,
        model_brier=macro_model,
        market_brier=macro_market,
        min_events=min_races,
        oos_only=oos_only,
        oos_declared=oos_declared,
        venue_mapped_pct=venue.get("mapped_pct"),
        target_kind=target,
    )

    ts = generated_at or datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "product": "racing",
        "generated_at": ts,
        "target": {
            "kind": target,
            "place_positions": races[0].place_positions if target == "place" else None,
            "description": (
                "Single winner per race — macro Brier (1/R) sum (f_i - o_i)^2"
                if target == "win"
                else "Place finisher — binary Brier per runner on P(place); multiple placers allowed"
            ),
        },
        "window": {
            "type": "rolling",
            "min_events": min_races,
            "n_events": n_races,
            "n_paired_market": sum(1 for r in races if r.market_probs() is not None),
            "oos_only": oos_only,
            "oos_declared": oos_declared,
            "train_cutoff": train_cutoff,
        },
        "model": {
            "macro_brier_per_race": round(macro_model, 4) if macro_model is not None else None,
            "murphy_runner_legs": model_eval["murphy"],
            "calibration_all_legs": model_eval["calibration_all_legs"],
        },
        "market": {
            "macro_brier_per_race": round(macro_market, 4) if macro_market is not None else None,
            "murphy_runner_legs": market_eval.get("murphy"),
            "n_runner_legs": market_eval.get("n", 0),
        },
        "delta_vs_market": {
            "macro_brier_per_race": delta,
            "verdict": _verdict(delta),
        },
        "venue_mapping": venue,
        "gates": gates,
    }
