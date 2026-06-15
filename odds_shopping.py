"""Line shopping: parse odds, classify books, shop by channel, build bet links."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from bookmakers import bookmaker_url, classify_bookmaker

MarketKey = str  # Home | Draw | Away | Over2.5 | BTTS
ChannelKey = Literal["all", "exchange", "soft", "sharp"]
SPORT_FOOTBALL = "football"
SPORT_RACING = "racing"

MARKETS_1X2_GOALS = ("Home", "Draw", "Away", "Over2.5", "BTTS")


@dataclass
class OddsOffer:
    market: MarketKey
    odds: float
    bookmaker: str
    bookmaker_id: Optional[int] = None
    source: str = "api-football"
    category: str = "unknown"
    bet_url: str = ""
    sport: str = SPORT_FOOTBALL
    event_label: str = ""
    selection_label: str = ""

    def to_quote(self) -> Dict[str, Any]:
        return {
            "odds": self.odds,
            "bookmaker": self.bookmaker,
            "bookmaker_id": self.bookmaker_id,
            "source": self.source,
            "category": self.category,
            "bet_url": self.bet_url,
        }


def _empty_quote() -> Dict[str, Any]:
    return {
        "odds": 0.0,
        "bookmaker": "",
        "bookmaker_id": None,
        "source": "",
        "category": "",
        "bet_url": "",
    }


def _best_offer(offers: List[OddsOffer]) -> Dict[str, Any]:
    if not offers:
        return _empty_quote()
    top = max(offers, key=lambda o: o.odds)
    return top.to_quote()


def _parse_market_value(market_name: str, value: str) -> Optional[MarketKey]:
    name = market_name.lower()
    val = value.lower()
    if "match winner" in name or name in ("1x2", "home/draw/away"):
        if val == "home":
            return "Home"
        if val == "draw":
            return "Draw"
        if val == "away":
            return "Away"
    if "over/under" in name and "2.5" in val and "over" in val:
        return "Over2.5"
    if "both teams score" in name and val == "yes":
        return "BTTS"
    return None


def parse_api_football_odds(
    odds_json: Dict[str, Any],
    *,
    event_label: str = "",
    sport: str = SPORT_FOOTBALL,
) -> List[OddsOffer]:
    """Flatten API-Football odds payload into normalised offers."""
    offers: List[OddsOffer] = []
    try:
        bookmakers = odds_json["response"][0]["bookmakers"]
    except (KeyError, IndexError, TypeError):
        return offers

    for b in bookmakers:
        bname = str(b.get("name") or "Unknown")
        bid = b.get("id")
        bid_int = int(bid) if bid is not None else None
        cat = classify_bookmaker(bname, bid_int)
        for market in b.get("bets", []):
            mname = str(market.get("name", ""))
            for outcome in market.get("values", []):
                val = str(outcome.get("value", ""))
                mkey = _parse_market_value(mname, val)
                if mkey is None:
                    continue
                try:
                    odd = float(outcome.get("odd"))
                except (TypeError, ValueError):
                    continue
                if odd <= 1.0:
                    continue
                offers.append(
                    OddsOffer(
                        market=mkey,
                        odds=odd,
                        bookmaker=bname,
                        bookmaker_id=bid_int,
                        source="api-football",
                        category=cat,
                        bet_url=bookmaker_url(
                            bname,
                            sport=sport,
                            event_label=event_label,
                            bookmaker_id=bid_int,
                        ),
                        sport=sport,
                        event_label=event_label,
                        selection_label=val,
                    )
                )
    return offers


def parse_odds_api_h2h(
    events: List[Dict[str, Any]],
    *,
    sport: str = SPORT_FOOTBALL,
) -> List[OddsOffer]:
    """Parse The Odds API h2h (1X2) events into offers."""
    offers: List[OddsOffer] = []
    for ev in events:
        home = str(ev.get("home_team") or "")
        away = str(ev.get("away_team") or "")
        label = f"{home} v {away}".strip()
        for book in ev.get("bookmakers", []):
            bname = str(book.get("title") or book.get("key") or "Unknown")
            bid = book.get("key")
            cat = classify_bookmaker(bname)
            for market in book.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for out in market.get("outcomes", []):
                    name = str(out.get("name") or "")
                    try:
                        odd = float(out.get("price"))
                    except (TypeError, ValueError):
                        continue
                    if odd <= 1.0:
                        continue
                    if name == home:
                        mkey: MarketKey = "Home"
                    elif name == away:
                        mkey = "Away"
                    elif name.lower() == "draw":
                        mkey = "Draw"
                    else:
                        continue
                    direct = str(out.get("link") or book.get("link") or "")
                    offers.append(
                        OddsOffer(
                            market=mkey,
                            odds=odd,
                            bookmaker=bname,
                            bookmaker_id=None,
                            source="the-odds-api",
                            category=cat,
                            bet_url=bookmaker_url(
                                bname,
                                sport=sport,
                                event_label=label,
                                direct_link=direct,
                            ),
                            sport=sport,
                            event_label=label,
                            selection_label=name,
                        )
                    )
    return offers


def parse_odds_api_racing_win(events: List[Dict[str, Any]]) -> List[OddsOffer]:
    """Parse The Odds API horse-racing win markets (per-runner offers)."""
    offers: List[OddsOffer] = []
    for ev in events:
        venue = str(ev.get("sport_title") or "Racing")
        commence = str(ev.get("commence_time") or "")[:16].replace("T", " ")
        label = f"{venue} {commence}".strip()
        for book in ev.get("bookmakers", []):
            bname = str(book.get("title") or book.get("key") or "Unknown")
            cat = classify_bookmaker(bname)
            for market in book.get("markets", []):
                if market.get("key") not in ("win", "outrights"):
                    continue
                for out in market.get("outcomes", []):
                    runner = str(out.get("name") or "")
                    if not runner:
                        continue
                    try:
                        odd = float(out.get("price"))
                    except (TypeError, ValueError):
                        continue
                    if odd <= 1.0:
                        continue
                    mkey = f"Win:{runner}"
                    direct = str(out.get("link") or book.get("link") or "")
                    offers.append(
                        OddsOffer(
                            market=mkey,
                            odds=odd,
                            bookmaker=bname,
                            source="the-odds-api",
                            category=cat,
                            bet_url=bookmaker_url(
                                bname,
                                sport=SPORT_RACING,
                                event_label=f"{label} {runner}",
                                direct_link=direct,
                            ),
                            sport=SPORT_RACING,
                            event_label=label,
                            selection_label=runner,
                        )
                    )
    return offers


def shop_lines(
    offers: List[OddsOffer],
    *,
    markets: Optional[tuple[str, ...]] = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Best quote per market for all / exchange / soft / sharp channels."""
    keys = markets or MARKETS_1X2_GOALS
    by_market: Dict[str, List[OddsOffer]] = {k: [] for k in keys}
    for o in offers:
        if o.market in by_market:
            by_market[o.market].append(o)

    result: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for mkey, moffers in by_market.items():
        result[mkey] = {
            "all": _best_offer(moffers),
            "exchange": _best_offer([o for o in moffers if o.category == "exchange"]),
            "soft": _best_offer([o for o in moffers if o.category in ("soft", "unknown")]),
            "sharp": _best_offer([o for o in moffers if o.category == "sharp"]),
        }
    return result


def shop_racing_winners(offers: List[OddsOffer]) -> List[Dict[str, Any]]:
    """Per-runner best all / exchange / soft prices for racing."""
    runners: Dict[str, List[OddsOffer]] = {}
    for o in offers:
        if not o.market.startswith("Win:"):
            continue
        runner = o.market[4:]
        runners.setdefault(runner, []).append(o)

    rows: List[Dict[str, Any]] = []
    for runner, roffers in sorted(runners.items()):
        event = roffers[0].event_label if roffers else ""
        shopped = shop_lines(roffers, markets=(roffers[0].market,))
        q = shopped[roffers[0].market]
        rows.append(
            {
                "event": event,
                "runner": runner,
                "best_odds": q["all"]["odds"],
                "best_book": q["all"]["bookmaker"],
                "best_url": q["all"]["bet_url"],
                "exchange_odds": q["exchange"]["odds"],
                "exchange_book": q["exchange"]["bookmaker"],
                "exchange_url": q["exchange"]["bet_url"],
                "soft_odds": q["soft"]["odds"],
                "soft_book": q["soft"]["bookmaker"],
                "soft_url": q["soft"]["bet_url"],
                "sources": ", ".join(sorted({o.source for o in roffers})),
            }
        )
    rows.sort(key=lambda r: (-(r["best_odds"] or 0), r["runner"]))
    return rows


def extract_best(odds_json: Dict[str, Any], *, event_label: str = "") -> Dict[str, Dict[str, Any]]:
    """Backward-compatible: best overall odds per market (with bookmaker metadata)."""
    offers = parse_api_football_odds(odds_json, event_label=event_label)
    shopped = shop_lines(offers)
    return {m: shopped[m]["all"] for m in MARKETS_1X2_GOALS}


def pick_channel_quote(
    shopped: Dict[str, Dict[str, Dict[str, Any]]],
    market: str,
    channel: ChannelKey,
) -> Dict[str, Any]:
    """Select quote for value scan; falls back to 'all' when channel empty."""
    ch = shopped.get(market, {})
    quote = ch.get(channel) or _empty_quote()
    if quote.get("odds", 0) <= 1.0 and channel != "all":
        quote = ch.get("all") or _empty_quote()
    return quote
