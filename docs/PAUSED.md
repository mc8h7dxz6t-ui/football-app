# FVE paused (shared API keys with hibs-bet)

**Status:** Live ingest is **off** so we do not compete with `hibs-bet` / `hibs-betting-app` for
API-Football, Odds API, or Matchbook quota.

**Production data path:** `~/Applications` → [hibs-bet](https://github.com/mc8h7dxz6t-ui/hibs-bet) → https://hibs-bet.co.uk

## Do not start

- `bash scripts/run_stack.sh`
- `docker compose up` (worker / auto watchlist)
- `python worker.py --auto`

## Safe to use (no live book polling)

- `pytest -q`
- `python3 run_backtest.py --simulate 6000`
- Matchbook-only arb shadow: `docs/ARB_FREEZE.md` + `docker compose --profile arb-shadow up`

## Resume later (pick one)

1. Dedicated `API_SPORTS_KEY` for FVE only, or  
2. Hibs exposes lines → FVE consumes `FVE_API_URL` (no duplicate ingest)

Unset pause:

```bash
# .env
FVE_PAUSED=0
```
