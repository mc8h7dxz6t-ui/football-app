# Scrape-heavy FVE (zero paid APIs on this host)

Use when you **cannot afford** API-Football / Odds API / Matchbook keys on FVE.
Hibs-bet (or your collector) does the expensive work; FVE reads **scraped JSON + FotMob public data**.

## Quick start

```bash
# .env
FVE_PAUSED=0
FVE_FEED_MODE=scrape
FVE_SCRAPE_HEAVY=1
FVE_SCRAPE_LINES_DIR=/workspace/data/scrape-lines
HIBS_UPSTREAM_BASE_URL=https://hibs-bet.co.uk   # optional — collector uses this

# One command
bash scripts/run_scrape_stack.sh
```

## What runs (no paid API on FVE)

| Layer | Source | Cost |
|-------|--------|------|
| **Lines** | `hibs-upstream` → `scrape-file` → `scrape-cache` | Free on FVE |
| **Watchlist** | FotMob public JSON (`FVE_SCRAPE_WATCHLIST` auto when no API key) | Free |
| **Sports/stats** | FotMob league tables | Free |
| **hibs-bet** | Own stack (may use APIs there — not on FVE) | Separate host |

## Feed chain (`FVE_FEED_MODE=scrape`)

Default: `hibs-upstream,scrape-file,scrape-cache`

1. **hibs-upstream** — `GET /api/fve/lines/{fixture}` (if `HIBS_UPSTREAM_BASE_URL` set)
2. **scrape-file** — JSON files in `FVE_SCRAPE_LINES_DIR`
3. **scrape-cache** — optional HTTP sidecar (`FVE_SCRAPE_LINES_URL`)

## Collector cron (recommended)

```bash
# Every 5 min — pull hibs lines into scrape dir
*/5 * * * * cd /opt/fve && FVE_SCRAPE_LINES_DIR=/var/lib/fve/scrape-lines \
  python3 scripts/fve_hibs_lines_collector.py --from-watchlist >> /var/log/fve-collector.log 2>&1
```

Install hibs proxy first: `scripts/vps_install_hibs_fve_lines.sh`

## Manual fixtures (no watchlist)

```bash
WATCHLIST_FIXTURES="Arsenal v Chelsea,Liverpool v Man City"
FVE_AUTO_WATCHLIST=0
python3 worker.py --fixtures "$WATCHLIST_FIXTURES"
```

## File format (`data/scrape-lines/Arsenal_v_Chelsea.json`)

Same as hibs `/api/fve/lines` — see `docs/SEPARATE_FEEDS.md`.

## vs `FVE_UPSTREAM_MODE=hibs`

| Mode | When |
|------|------|
| `FVE_FEED_MODE=scrape` | No API keys; file cache + optional hibs poll; FotMob watchlist |
| `FVE_UPSTREAM_MODE=hibs` | Live poll hibs only (simpler, no collector cron) |

Both avoid **paid APIs on FVE**. Scrape mode adds **offline resilience** (files still work if hibs blips).

## Ops

```bash
curl -sS localhost:8000/health | jq '.feed_mode,.feed_chain,.worker'
python3 scripts/fve_hibs_lines_collector.py --fixtures "Arsenal v Chelsea"
```
