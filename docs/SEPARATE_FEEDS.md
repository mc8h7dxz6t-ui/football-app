# Separate FVE feed stack — scrape sidecar + API backups (no hibs-bet upstream)

Use when FVE should run **independently** with its own keys and optional scrape sidecar.

## Modes

| `FVE_FEED_MODE` | Behaviour |
|-----------------|-----------|
| *(unset)* | Direct feeds: Matchbook + API-Football + optional Odds API |
| `hibs` / `FVE_UPSTREAM_MODE=hibs` | Poll hibs-bet `/api/fve/lines` only |
| **`separate`** | Prioritized chain via single `composite` feed |

## Separate stack (recommended)

```bash
FVE_PAUSED=0
FVE_FEED_MODE=separate
FVE_FEED_CHAIN=matchbook,odds-backup,api-football,scrape-cache

# Own keys — not shared with hibs-bet
MATCHBOOK_USERNAME=...
MATCHBOOK_PASSWORD=...
API_SPORTS_KEY=...
ODDS_API_KEY=...

# Optional scrape sidecar (HTTP — no HTML in FVE process)
FVE_SCRAPE_LINES_URL=http://127.0.0.1:8091/lines/{fixture_key}
FVE_SCRAPE_LINES_TOKEN=optional-secret
```

### Chain behaviour

1. **matchbook** — exchange prices (fast poll via `FEED_POLL_SEC_MATCHBOOK=0.5`)
2. **odds-backup** — The Odds API with fallback sport keys (`FVE_ODDS_BACKUP_SPORT_KEYS`)
3. **api-football** — soft book rows when APIs thin
4. **scrape-cache** — last resort; reads JSON from your sidecar only

Stops when Home/Draw/Away all have quotes. Hourly budgets apply per source (`FVE_*_MAX_CALLS_PER_HOUR`).

## Scrape sidecar pattern

FVE does **not** run HTML scrapers in-process. Run scrape risk in a separate cron/service:

```
[ Your scraper / oddschecker / custom ] → JSON file or HTTP
                                              ↓
                              FVE scrape-cache feed polls URL
                                              ↓
                                    Redis → WS → UI
```

Expected JSON (either shape):

```json
{
  "best_odds_1x2": {"home": 2.1, "draw": 3.4, "away": 3.2},
  "best_odds_source": {"home": "Bet365", "draw": "Bet365", "away": "Bet365"},
  "scrape_source": "my-scraper-v1"
}
```

Or `all_bookmaker_odds: [{ "bookmaker": "Bet365", "home": 2.1, ... }]`.

Example stub server: `scripts/fve_scrape_sidecar_stub.py`

## vs hibs-bet scraping

hibs-bet already runs **odds thin rescue** (Odds API backup keys + line-shop fill) inside its aggregator.
FVE `separate` mode replicates that **without** coupling to hibs deploy — useful for:

- Dedicated FVE VPS
- Research stack with own quota
- Scrape sidecar you control (ToS / IP risk isolated from main site)

## Legal / ops

- HTML scraping bookmakers may violate ToS — use sidecar + opt-in only
- Prefer Matchbook + licensed APIs for production
- Keep `FVE_PAUSED=1` until keys and chain are configured
