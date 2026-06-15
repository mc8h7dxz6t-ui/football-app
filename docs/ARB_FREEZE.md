# Arb freeze / unfreeze ladder

Frozen-by-default FVE can still **poll Matchbook** for arb research without touching hibs-bet’s API-Sports quota.

## Ladder

| Stage | What runs | Key env |
|-------|-----------|---------|
| **0 — Full freeze** | Nothing live | `FVE_PAUSED=1` (default) |
| **1 — Shadow scan** | Matchbook ingest + arb log, **no orders** | `FVE_ARB_ONLY=1`, `DISABLED_FEEDS=api-football,betfair,pinnacle,the-odds-api`, `MATCHBOOK_KILL_SWITCH=1` |
| **2 — Dry-run execute** | Same ingest; executor builds offers but does not submit | `MATCHBOOK_KILL_SWITCH=0`, `MATCHBOOK_AUTO_TRADE=0` |
| **3 — Micro live** | Small capped stakes on Matchbook | `MATCHBOOK_AUTO_TRADE=1`, `MATCHBOOK_CONFIRM_LIVE=YES`, keep retail caps |
| **4 — Full ingest** | API-Football watchlist + all feeds | `FVE_PAUSED=0` + **dedicated** `API_SPORTS_KEY` or hibs upstream lines |

Stage 1–3 keep `FVE_PAUSED=1` so shared hibs keys stay untouched.

## Fixture list (no API-Sports)

With `FVE_ARB_ONLY=1`, the worker does **not** call API-Football. Provide fixtures via:

1. `config/matchbook_map.json` — copy from `config/matchbook_map.json.example`
2. `WATCHLIST_FIXTURES=Home v Away::matchbook_event_id` (middle field optional)

## Docker: `arb-shadow` profile

```bash
cp .env.example .env
# Set MATCHBOOK_USERNAME / MATCHBOOK_PASSWORD and matchbook_map.json
docker compose --profile arb-shadow up -d --build
```

Brings up **redis**, **api**, **worker-arb** (Matchbook ingest), **arb-scanner** (shadow arb log). Execution is blocked by `MATCHBOOK_KILL_SWITCH=1`.

Health:

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
docker compose logs -f worker-arb arb-scanner
```

## Coexistence with hibs-bet

- hibs-bet owns API-Sports on the shared key — keep `FVE_PAUSED=1` for full ingest.
- Matchbook exchange polling is separate quota (`FVE_MATCHBOOK_MAX_CALLS_PER_HOUR`).
- Partial dutch risk stays off unless `MATCHBOOK_ALLOW_PARTIAL_DUTCH=1`.

## Unfreeze mistakes to avoid

- `FVE_ARB_ONLY=1` with API-Football still enabled → worker **refuses** to start.
- `docker compose --profile ingest` while paused → worker exits unless `FVE_ARB_ONLY` + Matchbook-only feeds.
- Live trade without `MATCHBOOK_CONFIRM_LIVE=YES` → blocked by design.

See also: `docs/PAUSED.md`, `scripts/pause_fve.sh`.
