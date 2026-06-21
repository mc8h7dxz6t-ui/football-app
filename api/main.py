"""FastAPI institutional layer — UI never calls book APIs directly."""

from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field

from engine.book_consistency import cross_market_discrepancy
from engine.devig import devig_1x2, overround
from engine.fair_value import benchmark_vs_sharp
from engine.portfolio_kelly import apply_portfolio_kelly_to_picks
from model import edge, expected_goals, full_market_probs, goal_model, kelly, match_model
from pipeline.cache import get_cache
from pipeline.circuit_breaker import breakers
from pipeline.ingest import ingest_fixture, build_fixture_bundle, build_line_view, refresh_sports_context
from feeds.registry import build_default_registry
from api.ws_hub import get_ws_hub
from pipeline.line_bus import get_line_bus
from pipeline.rate_limits import get_budget


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_ws_hub().ensure_started()
    yield


app = FastAPI(
    title="Football Value Engine API",
    version="2.0.0",
    description="Decoupled ingest + cache + sharp benchmark (inst++ pipeline)",
    lifespan=lifespan,
)


class IngestRequest(BaseModel):
    fixture_key: str
    fixture_id: Optional[int] = None
    matchbook_event_id: Optional[int] = None
    home_team: str = ""
    away_team: str = ""
    event_label: str = ""
    league_name: str = ""


class ValueScanRequest(BaseModel):
    fixture_key: str
    home_stats: Dict[str, Any] = Field(default_factory=dict)
    away_stats: Dict[str, Any] = Field(default_factory=dict)
    league_code: str = ""
    ml_probs: Dict[str, float] = Field(default_factory=dict)
    ml_blend_weight: float = Field(default_factory=lambda: float(os.environ.get("FVE_ML_BLEND_WEIGHT", "0.33")))
    min_edge_pct: float = 2.5
    bankroll: float = 1000.0
    kelly_fraction: float = 0.25
    use_xg: bool = True
    shop_channel: str = "soft"


@app.get("/health")
def health() -> Dict[str, Any]:
    from execution.risk import RiskConfig
    from pipeline.codec import codec_name
    from pipeline.redis_factory import redis_backend_label

    from feeds.composite_feed import composite_feed_status
    from pipeline.worker_status import worker_status

    cache = get_cache()
    risk = RiskConfig()
    paused = os.environ.get("FVE_PAUSED", "0").strip().lower() in ("1", "true", "yes", "on")
    health: Dict[str, Any] = {
        "status": "ok",
        "paused": paused,
        "cache_backend": cache.backend,
        "line_bus": get_line_bus().backend,
        "wire_codec": codec_name(),
        "redis_backend": redis_backend_label(),
        "ws_max_pending_sends": int(os.environ.get("WS_MAX_PENDING_SENDS", "8")),
        "ws_delta_updates": os.environ.get("FVE_WS_DELTA_UPDATES", "1"),
        "ws_client_delta": os.environ.get("FVE_WS_CLIENT_DELTA", "0"),
        "feed_mode": os.environ.get("FVE_FEED_MODE") or (
            "scrape"
            if os.environ.get("FVE_SCRAPE_HEAVY", "").strip().lower() in ("1", "true", "yes", "on")
            or (os.environ.get("FVE_FEED_MODE") or "").strip().lower() in ("scrape", "scrape-heavy", "zero-api")
            else "hibs"
            if os.environ.get("FVE_UPSTREAM_MODE", "").strip().lower() in ("hibs", "hibs-bet", "upstream")
            else "direct"
        ),
        "feed_chain": composite_feed_status(),
        "worker": worker_status(),
        "feed_poll_sec_matchbook": os.environ.get("FEED_POLL_SEC_MATCHBOOK", ""),
        "ws": get_ws_hub().status(),
        "api_budgets": get_budget().status(),
        "breakers": breakers.all_status(),
        "execution": risk.status(),
    }
    try:
        from pipeline.backtest_health import backtest_health_slice

        health["backtest_slice"] = backtest_health_slice()
    except Exception:
        health["backtest_slice"] = {"available": False}
    try:
        from inplay.evidence import inplay_evidence_gates

        ie = inplay_evidence_gates()
        health["inplay_evidence"] = {
            "buyer_ready": ie.get("buyer_ready"),
            "evidence_grade": ie.get("evidence_grade"),
            "buyer_readiness_score": ie.get("buyer_readiness_score"),
            "gates_pass": sum(1 for g in ie.get("gates", []) if g.get("pass")),
            "gates_total": len(ie.get("gates", [])),
        }
    except Exception as exc:
        health["inplay_evidence"] = {
            "buyer_ready": False,
            "error": str(exc)[:120],
        }
    try:
        from engine.paper_ledger import ledger_health_slice, prematch_evidence_gates

        health["paper"] = ledger_health_slice()
        pe = prematch_evidence_gates()
        health["prematch_evidence"] = {
            "buyer_ready": pe.get("buyer_ready"),
            "evidence_grade": pe.get("evidence_grade"),
            "buyer_readiness_score": pe.get("buyer_readiness_score"),
            "gates_pass": sum(1 for g in pe.get("gates", []) if g.get("pass")),
            "gates_total": len(pe.get("gates", [])),
        }
    except Exception as exc:
        health["paper"] = {"enabled": False, "error": str(exc)[:120]}
    return health


@app.get("/api/evidence/prematch")
def prematch_evidence() -> Dict[str, Any]:
    from engine.paper_ledger import prematch_evidence_gates

    return prematch_evidence_gates()


@app.get("/api/paper/export.csv")
def paper_export_csv(days: int = 90) -> Any:
    from engine.paper_ledger import export_csv
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(export_csv(days=days), media_type="text/csv")


@app.post("/api/paper/settle")
def paper_settle(payload: Dict[str, Any]) -> Dict[str, Any]:
    from engine.paper_ledger import settle_open_picks

    results = payload.get("results") if isinstance(payload.get("results"), dict) else payload
    return settle_open_picks(results=results)


@app.get("/api/inplay/evidence")
def inplay_evidence() -> Dict[str, Any]:
    from inplay.evidence import inplay_evidence_gates

    return inplay_evidence_gates()


@app.get("/api/inplay/marks/{fixture_id}")
def inplay_marks(fixture_id: int) -> Dict[str, Any]:
    from inplay.router import fetch_exchange_marks

    return fetch_exchange_marks(fixture_id)


@app.post("/api/inplay/feed/{vendor}/binary")
async def inplay_feed_binary(vendor: str, request: Request) -> Dict[str, Any]:
    from inplay.feeds_binary import ingest_binary_payload

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty payload")
    return ingest_binary_payload(vendor, body)


@app.websocket("/ws/lines/{fixture_key}")
async def ws_lines(fixture_key: str, websocket: WebSocket) -> None:
    """Push line + sports bundle — clients subscribe instead of polling book APIs."""
    await get_ws_hub().run_session(fixture_key, websocket)


@app.websocket("/ws/fixture/{fixture_key}")
async def ws_fixture(fixture_key: str, websocket: WebSocket) -> None:
    """Alias for /ws/lines — full fixture bundle (odds lines + sports context)."""
    await get_ws_hub().run_session(fixture_key, websocket)


@app.get("/fixture/{fixture_key}")
def get_fixture_bundle(fixture_key: str) -> Dict[str, Any]:
    cache = get_cache()
    bundle = build_fixture_bundle(cache, fixture_key)
    if not bundle.get("ready", {}).get("lines") and not bundle.get("ready", {}).get("sports"):
        raise HTTPException(404, "No cached fixture data — POST /ingest first")
    return bundle


@app.get("/sports/{fixture_key}")
def get_sports(fixture_key: str) -> Dict[str, Any]:
    cache = get_cache()
    sports = cache.get_sports(fixture_key)
    if not sports:
        raise HTTPException(404, "No sports context — POST /ingest with fixture_id")
    return sports


@app.get("/lines/{fixture_key}")
def get_lines(fixture_key: str, peak: bool = True) -> Dict[str, Any]:
    cache = get_cache()
    if not cache.get_ticks(fixture_key):
        raise HTTPException(404, "No cached lines — POST /ingest first")
    from pipeline.ingest import build_line_view

    view = build_line_view(cache, fixture_key)
    if not peak:
        from pipeline.ingest import ticks_to_shopped, build_fixture_1x2_sharp_line

        ticks = cache.get_ticks(fixture_key)
        shopped = ticks_to_shopped(ticks)
        view["shopped"] = shopped
        view["sharp_fair_probs"] = build_fixture_1x2_sharp_line(shopped)
        view["use_peak_window"] = False
    return view


@app.get("/lines/{fixture_key}/history")
def get_line_history(fixture_key: str, since_sec: float = 30.0) -> Dict[str, Any]:
    cache = get_cache()
    import time

    since = time.time() - since_sec
    history = cache.get_tick_history(fixture_key, since=since)
    if not history and not cache.get_ticks(fixture_key):
        raise HTTPException(404, "No history for fixture")
    return {
        "fixture_key": fixture_key,
        "since_sec": since_sec,
        "tick_count": len(history),
        "ticks": [t.to_dict() for t in history[-500:]],
    }


@app.post("/ingest")
def ingest(req: IngestRequest) -> Dict[str, Any]:
    label = req.event_label or req.fixture_key
    ctx = {
        "fixture_id": req.fixture_id,
        "matchbook_event_id": req.matchbook_event_id,
        "home_team": req.home_team,
        "away_team": req.away_team,
        "event_label": label,
        "league_name": req.league_name,
    }
    return ingest_fixture(build_default_registry(), req.fixture_key, context=ctx)


@app.post("/value-scan")
def value_scan(req: ValueScanRequest) -> Dict[str, Any]:
    cache = get_cache()
    from pipeline.ingest import build_line_view

    view = build_line_view(cache, req.fixture_key)
    if not view.get("tick_count"):
        raise HTTPException(404, "No cached lines for fixture")
    shopped = view["shopped"]
    sharp_fair = view.get("sharp_fair_probs")

    home_stats = dict(req.home_stats)
    away_stats = dict(req.away_stats)
    if not home_stats or not away_stats:
        sports = cache.get_sports(req.fixture_key)
        if sports:
            home_stats = home_stats or dict(sports.get("home_stats") or {})
            away_stats = away_stats or dict(sports.get("away_stats") or {})
    if not home_stats or not away_stats:
        raise HTTPException(404, "No team stats — ingest with fixture_id or pass home_stats/away_stats")

    league = req.league_code or (cache.get_sports(req.fixture_key) or {}).get("league_name") or ""
    ml_w = float(req.ml_blend_weight) if req.ml_probs else 0.0

    probs = full_market_probs(
        home_stats,
        away_stats,
        use_xg=req.use_xg,
        league_code=str(league),
        ml_probs=req.ml_probs or None,
        ml_weight=ml_w,
    )
    lam_h, lam_a = expected_goals(home_stats, away_stats, use_xg=req.use_xg, league_code=str(league))
    book_xm = cross_market_discrepancy(
        shopped, model_lam_h=lam_h, model_lam_a=lam_a, league_code=str(league)
    )

    picks: List[Dict[str, Any]] = []
    for sel, prob in probs.items():
        from odds_shopping import pick_channel_quote

        quote = pick_channel_quote(shopped, sel, req.shop_channel)  # type: ignore[arg-type]
        odds = float(quote.get("odds") or 0)
        if odds <= 1.0:
            continue
        e = edge(prob, odds)
        bench = None
        if sharp_fair:
            bench = benchmark_vs_sharp(
                selection=sel,
                soft_odds=odds,
                sharp_line_probs=sharp_fair,
                model_prob=prob,
            )
        if e < req.min_edge_pct:
            continue
        if bench and bench.likely_hallucination:
            continue
        stake = req.bankroll * kelly(prob, odds) * req.kelly_fraction
        xm_hint = next((h for h in book_xm.get("synthetic_hints", []) if h.get("market") == sel), None)
        picks.append(
            {
                "pick_id": str(uuid.uuid4()),
                "market": sel,
                "odds": odds,
                "bookmaker": quote.get("bookmaker"),
                "bet_url": quote.get("bet_url"),
                "model_prob": prob,
                "edge_pct": e,
                "stake": stake,
                "sharp_fair_prob": bench.sharp_fair_prob if bench else None,
                "edge_vs_sharp_pct": bench.edge_vs_sharp_pct if bench else None,
                "cross_market_hint": xm_hint,
            }
        )
    picks.sort(key=lambda p: p["edge_pct"], reverse=True)
    picks = apply_portfolio_kelly_to_picks(picks, bankroll=req.bankroll)
    scan_meta = {
        "league_code": str(league),
        "pricing_pipeline": "attack_defence"
        if os.environ.get("FVE_ATTACK_DEFENCE", "1") not in ("0", "false")
        else "venue_blend",
        "min_edge_pct": req.min_edge_pct,
    }
    ledger_result = None
    try:
        from engine.paper_ledger import ledger_enabled, pick_verification_hash, record_value_picks

        if ledger_enabled():
            for p in picks:
                ts = str(int(time.time()))
                p["verification_hash"] = pick_verification_hash(
                    p["pick_id"],
                    ts,
                    req.fixture_key,
                    p["market"],
                    float(p["odds"]),
                    float(p["stake"]),
                )
            ledger_result = record_value_picks(req.fixture_key, picks, scan_meta=scan_meta)
    except Exception:
        ledger_result = {"error": "ledger_record_failed"}

    return {
        "fixture_key": req.fixture_key,
        "picks": picks,
        "sharp_fair_probs": sharp_fair,
        "expected_goals": {"home": round(lam_h, 3), "away": round(lam_a, 3)},
        "league_code": str(league),
        "pricing_pipeline": "attack_defence" if os.environ.get("FVE_ATTACK_DEFENCE", "1") not in ("0", "false") else "venue_blend",
        "book_cross_market": book_xm,
        "model_probs": {k: round(v, 4) for k, v in probs.items()},
        "paper_ledger": ledger_result,
    }


class ArbExecuteRequest(BaseModel):
    fixture_key: str
    opportunity_index: int = 0
    total_outlay: Optional[float] = None


@app.get("/arb/{fixture_key}")
def list_arbs(fixture_key: str, min_profit_pct: float = 0.3) -> Dict[str, Any]:
    from engine.arb import scan_arbitrage

    cache = get_cache()
    ticks = cache.get_peak_ticks(fixture_key)
    if not ticks:
        raise HTTPException(404, "No lines cached for fixture")
    opps = scan_arbitrage(ticks, fixture_key=fixture_key, min_profit_pct=min_profit_pct)
    return {"fixture_key": fixture_key, "count": len(opps), "opportunities": [o.to_dict() for o in opps]}


@app.post("/arb/execute")
def execute_arb(req: ArbExecuteRequest) -> Dict[str, Any]:
    from engine.arb import scan_arbitrage
    from execution.matchbook_executor import execute_matchbook_arb
    from execution.risk import RiskConfig

    cache = get_cache()
    ticks = cache.get_peak_ticks(req.fixture_key)
    if not ticks:
        raise HTTPException(404, "No lines cached")
    opps = scan_arbitrage(ticks, fixture_key=req.fixture_key)
    if req.opportunity_index >= len(opps):
        raise HTTPException(404, "Opportunity index out of range")
    result = execute_matchbook_arb(
        opps[req.opportunity_index],
        risk=RiskConfig(),
        total_outlay=req.total_outlay,
    )
    return result.to_dict()


@app.get("/devig/demo")
def devig_demo(home: float = 2.1, draw: float = 3.4, away: float = 3.8) -> Dict[str, Any]:
    odds = {"Home": home, "Draw": draw, "Away": away}
    return {
        "overround_pct": round(overround(odds) * 100, 3),
        "proportional": devig_1x2(home, draw, away, method="proportional"),
        "power": devig_1x2(home, draw, away, method="power"),
        "shin": devig_1x2(home, draw, away, method="shin"),
    }


def run() -> None:
    import uvicorn

    port = int(os.environ.get("FVE_API_PORT", "8000"))
    uvicorn.run("api.main:app", host="0.0.0.0", port=port, reload=False)
