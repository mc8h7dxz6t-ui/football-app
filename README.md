# Football Value Engine

A Streamlit app that scans upcoming fixtures across UK and European leagues, prices
1X2 / Over 2.5 / BTTS markets with a coherent goals model, and flags **value bets**
(positive edge vs the best available bookmaker odds) with fractional-Kelly stake sizing.
Includes a **calibration backtest** to check whether the model actually has signal.

## Setup

```bash
pip install -r requirements.txt          # app
pip install -r requirements-dev.txt      # app + pytest (for tests)
```

Provide an [API-Football](https://www.api-football.com) key (free tier works) via an
environment variable **or** Streamlit secrets — it is never hard-coded:

```bash
export API_SPORTS_KEY="your_key_here"
export ODDS_API_KEY="your_key_here"   # optional — extra books + racing shop
```

or copy `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` (gitignored) and fill it in.

> ⚠️ **Security:** an API key was previously hard-coded in this file's history. If you
> forked/cloned before this change, **rotate your API-Football key** — anything committed
> to a public repo should be treated as compromised.

## Run

```bash
streamlit run app.py
```

### Inst++ pipeline (decoupled ingest)

The UI should **not** call book APIs directly in production. Use the FastAPI + worker stack:

```bash
pip install -r requirements-pro.txt
docker compose up -d redis postgres   # optional but recommended

export API_SPORTS_KEY="..."
export MATCHBOOK_USERNAME="..."       # your Matchbook API access
export MATCHBOOK_PASSWORD="..."
export REDIS_URL="redis://localhost:6379/0"

uvicorn api.main:app --port 8000
python worker.py --fixtures "Arsenal v Chelsea:12345:67890"
# tiered polls: Matchbook ~1s, API-Football ~5s (override: FEED_POLL_SEC_MATCHBOOK=0.5)
streamlit run app.py   # enable "Use FastAPI ingest layer" in sidebar
```

```
Matchbook / API-Football feeds
        → worker (poll) → Redis dedupe cache
        → Shin de-vig sharp synthetic line
        → Postgres snapshots (optional)
        → FastAPI /ingest /lines /value-scan
        → Streamlit / React frontend
```

**Sharp benchmark:** picks where your Poisson model shows edge but the **de-vigged
exchange/sharp line** disagrees are flagged as likely hallucinations and filtered out.

**Circuit breakers:** failed feeds fall back to cached lines instead of crashing the UI.

- **Value Scan** — set bankroll / min edge % / Kelly fraction (sidebar), pick leagues and
  season, choose days ahead, then **Run Scan**. Results show **bookmaker**, **exchange vs
  soft prices**, **bet links**, and edge sorted by your chosen shopping channel.
- **Racing Shop** — win-market line shop (UK / US / AU) when `ODDS_API_KEY` is set.
- **Backtest** — replays recent finished fixtures, settles them against real scores, and
  reports **Brier score, log loss, top-pick accuracy** and a **calibration table**.

## How it works (`model.py`)

A single **independent-Poisson** model drives every market, so 1X2 / Over 2.5 / BTTS
can't contradict each other:

- **expected_goals** — venue-aware: home team's home attack + away team's away defence,
  with **shrinkage** toward overall form when the venue sample is thin. When **xG** fields
  are present it blends xG with actual goals (`XG_BLEND_ALPHA`, default 0.6) — xG is more
  stable/predictive than raw goals. Toggle "Blend xG" in the sidebar (`use_xg`).
- **match_model / goal_model** — 1X2, Over 2.5 and BTTS from the same Poisson score grid.
- **Line shopping** (`odds_shopping.py`) — best price per market with **bookmaker name**,
  **exchange vs soft vs sharp** channels, **place-bet links**, and optional merge with
  **The Odds API** for broader football + **horse racing** coverage.
- **edge / kelly** — expected value % and fractional-Kelly stake.

API responses are cached (`st.cache_data`) to respect free-tier rate limits, and every
request is timeout-guarded and failure-tolerant.

## Backup xG sources (`xg_sources.py`)

The model blends xG into form when xG fields are present. Sources, in order of practicality:

| Source | Coverage | Cost / notes |
|--------|----------|--------------|
| **Understat** (via `soccerdata`) | **Big-5** (EPL, La Liga, Bundesliga, Serie A, Ligue 1) | ✅ Free, reliable, includes home/away xG. **Wired in** (`fetch_understat_team_xg`). |
| **FBref / StatsBomb** (via `soccerdata`) | Broad (Championship, Eredivisie, Primeira, Scottish Prem, …) | Needs a browser/anti-scrape layer (FBref returns 403 to plain requests); rate-limited. Next step for non-big-5 coverage. |
| **API-Football** fixture statistics | Wherever API-Football has it | Primary but **quota-heavy** (1 call per fixture). |

Enable the backup:

```bash
pip install -r requirements-xg.txt     # soccerdata (heavy, optional — imported lazily)
```

With **Blend xG** on (sidebar) the app pulls Understat season xG for big-5 leagues,
matches teams to the table by normalised name, and blends it into expected goals.
Non-big-5 leagues degrade gracefully to goals-only. Verified live (EPL 2025/26 → 20
teams, e.g. Man City ~2.1 xGF/g, Arsenal ~0.9 xGA/g).

## Calibration (`backtest.py`)

Pure, dependency-free metrics: `brier_score_1x2`, `log_loss_1x2`, `top_pick_accuracy`,
`calibration_table`, and `evaluate`. The uniform 1/3 baseline Brier is ~0.667 — beating
it is the bar for "the model has signal."

The in-app **Backtest** tab can also compare the model against the **de-vigged market**
(`evaluate_vs_market`) and run a flat-stake **value-bet ROI** (`roi_backtest`).

> The in-app backtest uses the *current* league table, so it is mildly in-sample
> (approximate). Treat it as a sanity signal, not a forward backtest.

### Headless runner + engine validation (`run_backtest.py`)

```bash
# Live (needs a valid key): model vs market + ROI over recent finished fixtures
API_SPORTS_KEY=... python run_backtest.py --days 14 --leagues 39,140 --with-odds

# Deterministic simulation (no API) — validates the engine + backtest end-to-end
python run_backtest.py --simulate 6000 --market-noise 0.0    # sharp book
python run_backtest.py --simulate 6000 --market-noise 0.10   # soft book
```

**What the simulation shows** (true rates generate scores; the model only sees a noisy
season; the market is the de-vigged truth ± `market-noise`):

| Metric | Result |
|--------|--------|
| Model Brier vs uniform 0.667 | **~0.648** — real signal (beats a coin flip) |
| Model vs **sharp** market Brier | market ~0.636 < model — a sharp book is sharper (expected) |
| Calibration | well-calibrated to ~50%, **overconfident above 50%** |
| Value ROI vs **sharp** book | **~ −2%** (you only pay the vig) |
| Value ROI vs **soft** book (noise 0.10) | **~ +10%** (model exploits mispricing) |

Takeaway: the engine has genuine signal but won't beat a sharp market — its edge is in
finding **soft/mispriced lines**, which is exactly what the value scan + line-shopping
are for.

## Tests

```bash
pytest -q
```

Covers the model (venue rates, shrinkage, Poisson distribution, odds parsing, edge/Kelly)
and the calibration metrics (Brier, log loss, accuracy, calibration bins).

## Notes / limitations

- No xG or injuries yet — goals are modelled from scored/conceded form. A natural next
  step is to fold in per-fixture xG (extra API calls) and a true forward backtest using
  point-in-time tables.

## Disclaimer

Analytical and research tool only. Betting carries financial risk; past performance is
not indicative of future results. Validate on your own data before staking real money.
