#!/usr/bin/env python3
"""Pull lines from hibs-bet /api/fve/lines into FVE_SCRAPE_LINES_DIR (zero API keys on FVE).

  export HIBS_UPSTREAM_BASE_URL=https://hibs-bet.co.uk
  export FVE_SCRAPE_LINES_DIR=/var/lib/fve/scrape-lines
  python3 scripts/fve_hibs_lines_collector.py --from-watchlist

Cron every 5 min alongside worker — hibs does the heavy lifting; FVE reads files.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _fixture_filename(fixture_key: str) -> str:
    safe = re.sub(r"[^\w\s-]", "", fixture_key).strip().replace(" ", "_")
    return f"{safe}.json"


def fetch_lines(base: str, fixture_key: str, token: str) -> dict:
    enc = urllib.request.quote(fixture_key, safe="")
    url = f"{base.rstrip('/')}/api/fve/lines/{enc}"
    headers = {"User-Agent": "fve-hibs-collector/1.0", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError("non-object response")
    return data


def fetch_fixture_index(base: str, token: str) -> list[dict]:
    url = f"{base.rstrip('/')}/api/fve/fixtures"
    headers = {"User-Agent": "fve-hibs-collector/1.0", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError("non-object fixtures response")
    rows = data.get("fixtures") or []
    return [r for r in rows if isinstance(r, dict)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Collect hibs lines JSON into scrape dir")
    ap.add_argument("--fixtures", default="", help="comma fixture keys or use --from-watchlist")
    ap.add_argument("--from-watchlist", action="store_true", help="discover via FotMob (no API key)")
    args = ap.parse_args()

    out_dir = Path(os.environ.get("FVE_SCRAPE_LINES_DIR", "data/scrape-lines"))
    out_dir.mkdir(parents=True, exist_ok=True)
    base = (os.environ.get("HIBS_UPSTREAM_BASE_URL") or "https://hibs-bet.co.uk").strip()
    token = (os.environ.get("HIBS_UPSTREAM_TOKEN") or os.environ.get("FVE_LINES_TOKEN") or "").strip()

    keys: list[str] = []
    if args.from_watchlist:
        try:
            rows = fetch_fixture_index(base, token)
            keys = [str(r.get("fixture_key") or "").strip() for r in rows if str(r.get("fixture_key") or "").strip()]
        except (urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
            print(f"hibs /api/fve/fixtures failed ({exc!r}) — FotMob fallback", file=sys.stderr)
        if not keys:
            from scrapers.fotmob_client import discover_fixtures
            from config.fotmob_leagues import FOTMOB_LEAGUE_ID

            keys, _ = discover_fixtures(
                dict(FOTMOB_LEAGUE_ID), days_ahead=int(os.environ.get("FVE_WATCHLIST_DAYS", "3"))
            )
    if args.fixtures:
        keys.extend(k.strip() for k in args.fixtures.split(",") if k.strip())
    if not keys:
        print("No fixtures — pass --fixtures or --from-watchlist", file=sys.stderr)
        return 1

    ok = 0
    for fk in keys:
        try:
            payload = fetch_lines(base, fk, token)
            payload["scrape_source"] = "hibs-lines-collector"
            path = out_dir / _fixture_filename(fk)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            ok += 1
        except (urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
            print(f"skip {fk}: {exc}", file=sys.stderr)
    print(f"wrote {ok}/{len(keys)} → {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
