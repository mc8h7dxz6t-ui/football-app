"""Prematch value-scan paper ledger — SHA-256 audit trail (racing parity)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

_RECON_PATH = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data")),
    "paper_recon.json",
)

MIN_PAPER_ROWS = 25
CLV_BEAT_PASS_PCT = 50.0


def ledger_enabled() -> bool:
    return os.getenv("FVE_PAPER_LEDGER", "1").strip().lower() in ("1", "true", "yes", "on")


def pick_verification_hash(
    pick_id: str,
    created_at: str,
    fixture_key: str,
    market: str,
    odds: float,
    stake: float,
) -> str:
    payload = f"{pick_id}|{created_at}|{fixture_key}|{market}|{odds}|{stake}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_db():
    from db.store import _ensure_engine

    _ensure_engine()


def record_value_picks(
    fixture_key: str,
    picks: List[Dict[str, Any]],
    *,
    scan_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Persist value-scan picks with verification hashes."""
    if not ledger_enabled() or not picks:
        return {"recorded": 0, "skipped": not ledger_enabled()}

    _ensure_db()
    from db.models import PaperPick
    from db.store import _Session

    recorded = 0
    expected_keys: Set[str] = set()
    session = _Session()
    try:
        for row in picks:
            market = str(row.get("market") or "")
            if not market:
                continue
            pick_key = f"{fixture_key}|{market}"
            expected_keys.add(pick_key)
            pick_id = str(row.get("pick_id") or uuid.uuid4())
            created_at = _utc_iso()
            odds = float(row.get("odds") or 0)
            stake = float(row.get("stake") or 0)
            vhash = pick_verification_hash(pick_id, created_at, fixture_key, market, odds, stake)
            existing = (
                session.query(PaperPick)
                .filter(PaperPick.fixture_key == fixture_key, PaperPick.market == market, PaperPick.status == "open")
                .first()
            )
            meta = {
                "bookmaker": row.get("bookmaker"),
                "bet_url": row.get("bet_url"),
                "model_prob": row.get("model_prob"),
                "edge_pct": row.get("edge_pct"),
                "edge_vs_sharp_pct": row.get("edge_vs_sharp_pct"),
                "sharp_fair_prob": row.get("sharp_fair_prob"),
                "cross_market_hint": row.get("cross_market_hint"),
                "scan_meta": scan_meta or {},
            }
            if existing:
                existing.odds = odds
                existing.stake = stake
                existing.verification_hash = vhash
                existing.model_prob = row.get("model_prob")
                existing.edge_pct = row.get("edge_pct")
                existing.meta_json = json.dumps(meta, default=str)
                existing.updated_at = time.time()
            else:
                session.add(
                    PaperPick(
                        pick_id=pick_id,
                        fixture_key=fixture_key,
                        market=market,
                        odds=odds,
                        stake=stake,
                        verification_hash=vhash,
                        model_prob=row.get("model_prob"),
                        edge_pct=row.get("edge_pct"),
                        status="open",
                        meta_json=json.dumps(meta, default=str),
                        created_at=time.time(),
                        updated_at=time.time(),
                    )
                )
            recorded += 1
        session.commit()
    finally:
        session.close()

    _write_recon(fixture_key, expected_keys, recorded)
    return {"recorded": recorded, "fixture_key": fixture_key}


def _write_recon(fixture_key: str, expected_keys: Set[str], recorded: int) -> None:
    os.makedirs(os.path.dirname(_RECON_PATH), exist_ok=True)
    payload = {
        "ts": _utc_iso(),
        "fixture_key": fixture_key,
        "expected": sorted(expected_keys),
        "recorded": recorded,
        "is_clean": recorded == len(expected_keys),
    }
    with open(_RECON_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def last_recon() -> Dict[str, Any]:
    if not os.path.isfile(_RECON_PATH):
        return {"is_clean": True, "message": "no scans yet"}
    try:
        with open(_RECON_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {"is_clean": True}
    except Exception as exc:
        return {"is_clean": False, "error": str(exc)[:80]}


def ledger_stats() -> Dict[str, Any]:
    _ensure_db()
    from db.models import PaperPick
    from db.store import _Session

    session = _Session()
    try:
        rows = session.query(PaperPick).all()
        open_n = sum(1 for r in rows if r.status == "open")
        settled = sum(1 for r in rows if r.status == "settled")
        with_hash = sum(1 for r in rows if r.verification_hash)
        clv_beat = sum(1 for r in rows if r.clv_beat is True)
        clv_n = sum(1 for r in rows if r.clv_beat is not None)
        tier_counts: Dict[str, int] = {}
        for r in rows:
            if r.clv_benchmark_tier:
                tier_counts[r.clv_benchmark_tier] = tier_counts.get(r.clv_benchmark_tier, 0) + 1
        pinnacle_n = tier_counts.get("pinnacle", 0)
        return {
            "n_rows": len(rows),
            "open": open_n,
            "settled": settled,
            "with_verification_hash": with_hash,
            "clv_beat_n": clv_beat,
            "clv_n": clv_n,
            "clv_beat_pct": round(100.0 * clv_beat / clv_n, 1) if clv_n else None,
            "clv_benchmark_tiers": tier_counts,
            "clv_pinnacle_n": pinnacle_n,
            "clv_pinnacle_pct": round(100.0 * pinnacle_n / clv_n, 1) if clv_n else None,
        }
    finally:
        session.close()


def ledger_health_slice() -> Dict[str, Any]:
    stats = ledger_stats()
    recon = last_recon()
    is_clean = recon.get("is_clean", True)
    if stats["n_rows"] > 0 and recon.get("recorded") is not None:
        is_clean = bool(recon.get("is_clean"))
    return {
        "enabled": ledger_enabled(),
        "n_rows": stats["n_rows"],
        "open": stats["open"],
        "settled": stats["settled"],
        "with_verification_hash": stats["with_verification_hash"],
        "recon_clean": is_clean,
        "clv_beat_pct": stats.get("clv_beat_pct"),
        "clv_n": stats.get("clv_n"),
        "clv_benchmark_tiers": stats.get("clv_benchmark_tiers"),
        "clv_pinnacle_n": stats.get("clv_pinnacle_n"),
        "clv_pinnacle_pct": stats.get("clv_pinnacle_pct"),
    }


def settle_open_picks(*, results: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Settle open picks when results map provided:
      results[fixture_key] = {
        "home_goals": int, "away_goals": int,
        "closing_odds": {market: float},  # legacy
        "closing": {
          "pinnacle_1x2": {"home":..,"draw":..,"away":..},
          "exchange_1x2": {...},
          "api_football_1x2": {...},
          "all_bookmaker_odds": [...],
        },
      }
    """
    from engine.clv_benchmark import (
        parse_exchange_1x2_from_panel,
        parse_pinnacle_1x2_from_panel,
        resolve_clv_closing,
    )

    _ensure_db()
    from db.models import PaperPick
    from db.store import _Session

    results = results or {}
    settled = 0
    session = _Session()
    try:
        open_rows = session.query(PaperPick).filter(PaperPick.status == "open").all()
        for row in open_rows:
            res = results.get(row.fixture_key)
            if not res:
                continue
            hg = int(res.get("home_goals", 0))
            ag = int(res.get("away_goals", 0))
            if hg > ag:
                outcome = "home"
            elif ag > hg:
                outcome = "away"
            else:
                outcome = "draw"
            won = row.market.lower() == outcome
            closing_block = res.get("closing") if isinstance(res.get("closing"), dict) else {}
            panel = closing_block.get("all_bookmaker_odds") or res.get("all_bookmaker_odds")
            pinnacle_1x2 = closing_block.get("pinnacle_1x2") or (
                parse_pinnacle_1x2_from_panel(panel) if panel else None
            )
            exchange_1x2 = closing_block.get("exchange_1x2") or (
                parse_exchange_1x2_from_panel(panel) if panel else None
            )
            api_football_1x2 = closing_block.get("api_football_1x2") or closing_block.get("1x2")
            legacy = (res.get("closing_odds") or {}).get(row.market)
            closing, tier, source = resolve_clv_closing(
                row.market,
                pinnacle_1x2=pinnacle_1x2,
                exchange_1x2=exchange_1x2,
                api_football_1x2=api_football_1x2,
                legacy_market_odds=legacy,
            )
            clv_beat = None
            if closing and float(closing) > 1:
                clv_beat = float(row.odds) >= float(closing)
            pnl = (float(row.stake) * (float(row.odds) - 1)) if won else -float(row.stake)
            row.status = "settled"
            row.outcome = outcome
            row.won = won
            row.pnl = round(pnl, 4)
            row.closing_odds = closing
            row.clv_beat = clv_beat
            row.clv_benchmark_tier = tier
            row.clv_benchmark_source = source
            row.settled_at = time.time()
            settled += 1
        session.commit()
    finally:
        session.close()
    return {"settled": settled, "open_remaining": ledger_stats()["open"]}


def export_csv(*, days: int = 90) -> str:
    _ensure_db()
    from db.models import PaperPick
    from db.store import _Session

    cutoff = time.time() - days * 86400
    session = _Session()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "pick_id",
            "fixture_key",
            "market",
            "odds",
            "stake",
            "status",
            "verification_hash",
            "edge_pct",
            "model_prob",
            "outcome",
            "won",
            "pnl",
            "closing_odds",
            "clv_beat",
            "clv_benchmark_tier",
            "clv_benchmark_source",
            "created_at",
        ]
    )
    try:
        rows = (
            session.query(PaperPick)
            .filter(PaperPick.created_at >= cutoff)
            .order_by(PaperPick.created_at.desc())
            .all()
        )
        for r in rows:
            writer.writerow(
                [
                    r.pick_id,
                    r.fixture_key,
                    r.market,
                    r.odds,
                    r.stake,
                    r.status,
                    r.verification_hash,
                    r.edge_pct,
                    r.model_prob,
                    r.outcome or "",
                    r.won,
                    r.pnl,
                    r.closing_odds,
                    r.clv_beat,
                    r.clv_benchmark_tier,
                    r.clv_benchmark_source,
                    r.created_at,
                ]
            )
    finally:
        session.close()
    return buf.getvalue()


def prematch_evidence_gates() -> Dict[str, Any]:
    """V10–V14 prematch FVE evidence gates (local health + ledger)."""
    from pipeline.worker_status import worker_status

    worker = worker_status()
    paused = os.environ.get("FVE_PAUSED", "0").strip().lower() in ("1", "true", "yes", "on")
    paper = ledger_health_slice()
    stats = ledger_stats()

    v10 = worker.get("alive") is True or worker.get("status") == "ok"
    v11 = not paused
    v12 = int(paper.get("with_verification_hash") or 0) >= MIN_PAPER_ROWS
    v13 = paper.get("recon_clean") is True
    clv_n = int(stats.get("clv_n") or 0)
    clv_pct = stats.get("clv_beat_pct")
    v14 = clv_n >= MIN_PAPER_ROWS and clv_pct is not None and float(clv_pct) >= CLV_BEAT_PASS_PCT
    pin_pct = stats.get("clv_pinnacle_pct")
    v15 = True  # informational — Pinnacle-tier coverage disclosure

    gates = [
        {
            "id": "V10_worker",
            "label": "FVE ingest worker alive",
            "pass": v10,
            "actual": worker,
            "threshold": "worker alive",
            "critical": True,
        },
        {
            "id": "V11_unpaused",
            "label": "FVE not paused",
            "pass": v11,
            "actual": paused,
            "threshold": "paused=false",
            "critical": True,
        },
        {
            "id": "V12_paper",
            "label": "Prematch paper ledger rows",
            "pass": v12,
            "actual": paper.get("with_verification_hash"),
            "threshold": f"n>={MIN_PAPER_ROWS}",
            "critical": False,
            "n": paper.get("with_verification_hash"),
        },
        {
            "id": "V13_recon",
            "label": "Paper recon clean",
            "pass": v13,
            "actual": paper.get("recon_clean"),
            "threshold": "true",
            "critical": False,
        },
        {
            "id": "V14_clv",
            "label": "Prematch CLV beat-close",
            "pass": v14,
            "actual": {"n": clv_n, "beat_pct": clv_pct},
            "threshold": f"n>={MIN_PAPER_ROWS}, beat>={CLV_BEAT_PASS_PCT}%",
            "critical": False,
            "n": clv_n,
        },
        {
            "id": "V15_clv_benchmark",
            "label": "CLV benchmark tier disclosure",
            "pass": v15,
            "actual": {
                "tiers": stats.get("clv_benchmark_tiers"),
                "pinnacle_pct": pin_pct,
                "ladder": ["pinnacle", "exchange", "sharp_synthetic", "api_football"],
            },
            "threshold": "informational — Pinnacle close preferred",
            "message": "API-Football close is not equivalent to Pinnacle institutional benchmark.",
            "critical": False,
            "n": clv_n,
        },
    ]
    critical_pass = all(g["pass"] for g in gates if g.get("critical"))
    evidence_pass = all(g["pass"] for g in gates if not g.get("critical"))
    passed_n = sum(1 for g in gates if g["pass"])
    ratio = passed_n / max(len(gates), 1)

    if not critical_pass:
        grade = "D"
    elif evidence_pass:
        grade = "A"
    elif ratio >= 0.8:
        grade = "B"
    else:
        grade = "C"

    return {
        "vertical": "fve_prematch",
        "gates": gates,
        "critical_pass": critical_pass,
        "evidence_pass": evidence_pass,
        "evidence_grade": grade,
        "buyer_ready": critical_pass and evidence_pass,
        "buyer_readiness_score": round(100.0 * ratio, 1),
        "paper": paper,
    }
