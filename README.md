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
```

or copy `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` (gitignored) and fill it in.

> ⚠️ **Security:** an API key was previously hard-coded in this file's history. If you
> forked/cloned before this change, **rotate your API-Football key** — anything committed
> to a public repo should be treated as compromised.

## Run

```bash
streamlit run app.py
```

- **Value Scan** — set bankroll / min edge % / Kelly fraction (sidebar), pick leagues and
  season, choose days ahead, then **Run Scan**. Results are sorted by edge.
- **Backtest** — replays recent finished fixtures, settles them against real scores, and
  reports **Brier score, log loss, top-pick accuracy** and a **calibration table**.

## How it works (`model.py`)

A single **independent-Poisson** model drives every market, so 1X2 / Over 2.5 / BTTS
can't contradict each other:

- **expected_goals** — venue-aware: home team's home attack + away team's away defence,
  with **shrinkage** toward overall form when the venue sample is thin.
- **match_model / goal_model** — 1X2, Over 2.5 and BTTS from the same Poisson score grid.
- **extract_best** — best decimal odds per market across bookmakers (line shopping).
- **edge / kelly** — expected value % and fractional-Kelly stake.

API responses are cached (`st.cache_data`) to respect free-tier rate limits, and every
request is timeout-guarded and failure-tolerant.

## Calibration (`backtest.py`)

Pure, dependency-free metrics: `brier_score_1x2`, `log_loss_1x2`, `top_pick_accuracy`,
`calibration_table`, and `evaluate`. The uniform 1/3 baseline Brier is ~0.667 — beating
it is the bar for "the model has signal."

> The in-app backtest uses the *current* league table, so it is mildly in-sample
> (approximate). Treat it as a sanity signal, not a forward backtest.

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
