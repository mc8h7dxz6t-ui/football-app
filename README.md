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

### Inst++ pipeline (hands-off)

One command starts Redis, API, **auto watchlist worker**, and Streamlit:

```bash
cp .env.example .env    # set API_SPORTS_KEY (required)
bash scripts/run_stack.sh
```

Open http://localhost:8501 → sidebar **Use FastAPI ingest layer**. The worker discovers
upcoming fixtures from API-Football every hour (`FVE_WATCHLIST_DAYS=3` by default).

**Matchbook exchange lines:** copy `config/matchbook_map.json.example` → `config/matchbook_map.json`
and add `Home v Away` → event id keys.

**Daily corruption check:**

```bash
bash scripts/preflight_fve.sh
bash scripts/install_cron_fve.sh   # optional 06:15 UTC cron
```

Manual / dev mode:

```bash
pip install -r requirements-pro.txt
docker compose up -d redis
uvicorn api.main:app --port 8000
python worker.py --auto
# or: python worker.py --fixtures "Arsenal v Chelsea:12345:67890"
streamlit run app.py
```

```
Matchbook / API-Football feeds
        → async worker (250ms scheduler) → Redis ZSET tick rings
        → Shin de-vig sharp synthetic line
        → Postgres snapshots (optional)
        → FastAPI /ingest /lines /value-scan /ws/lines/{fixture}
        → Streamlit / React frontend
```

**WebSocket (no book API polling from UI):**

```bash
# ws://localhost:8000/ws/lines/Arsenal%20v%20Chelsea
# Send "ping" or "snapshot" as text; receive snapshot | update | pong
```

**Protect shared API keys** (Matchbook + Odds API used across repos):

```bash
export FVE_ODDS_API_MAX_CALLS_PER_HOUR=15      # conservative default
export FVE_MATCHBOOK_MAX_CALLS_PER_HOUR=1200
export ENABLE_ODDS_API_FEED=0                  # keep Odds API feed off unless needed
```

See `docs/ARCHITECTURE.md` for Institutional++ upgrade path vs current Python stack.

**Sharp benchmark:** picks where your Poisson model shows edge but the **de-vigged
exchange/sharp line** disagrees are flagged as likely hallucinations and filtered out.

**Circuit breakers:** failed feeds fall back to cached lines instead of crashing the UI.

### Matchbook arb execution (optional — real money)

Scans cross-book 1X2 dutch arbs and can place **Matchbook legs only** via Edge API.

```bash
# Always starts in DRY-RUN (logs offers, does not bet)
python arb_worker.py --fixtures "Arsenal v Chelsea:12345:67890" --execute

# LIVE — requires both flags + small stake caps
export MATCHBOOK_AUTO_TRADE=1
export MATCHBOOK_CONFIRM_LIVE=YES
export MATCHBOOK_MAX_STAKE=2.00
export MATCHBOOK_MAX_OUTLAY=6.00
export ARB_MIN_PROFIT_PCT=0.5
python arb_worker.py --fixtures "..." --execute
```

API: `GET /arb/{fixture_key}` · `POST /arb/execute`

> **Risk:** Partial dutch (legs on other books) is **not** locked profit unless you place all legs.
> Default blocks partial auto-exec unless `MATCHBOOK_ALLOW_PARTIAL_DUTCH=1`.

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
