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

1. **Dedicated keys** — `FVE_PAUSED=0` and your own `API_SPORTS_KEY` (and optional Matchbook / Odds API).
2. **Hibs upstream** — hibs-bet exposes cached lines; FVE polls `/api/fve/lines/{fixture}` (no book API quota burn):
   ```bash
   # On VPS (hibs-bet)
   curl -fsSL .../scripts/vps_install_hibs_fve_lines.sh | sudo bash
   ```
   ```bash
   # FVE .env
   FVE_PAUSED=0
   FVE_UPSTREAM_MODE=hibs
   HIBS_UPSTREAM_BASE_URL=https://hibs-bet.co.uk
   HIBS_UPSTREAM_TOKEN=   # optional — same as hibs FVE_LINES_TOKEN
   ```

3. **Separate stack** — own keys + backup chain + optional scrape sidecar (`docs/SEPARATE_FEEDS.md`):
   ```bash
   FVE_PAUSED=0
   FVE_FEED_MODE=separate
   ```
4. **Scrape-heavy (zero paid APIs on FVE)** — FotMob watchlist + file cache + optional hibs collector (`docs/SCRAPE_HEAVY.md`):
   ```bash
   FVE_PAUSED=0
   FVE_FEED_MODE=scrape
   FVE_SCRAPE_LINES_DIR=./data/scrape-lines
   bash scripts/run_scrape_stack.sh
   ```

Unset pause (dedicated-key path only):

```bash
# .env
FVE_PAUSED=0
```
