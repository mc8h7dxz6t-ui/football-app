# Football Value Engine

A Streamlit app that scans upcoming fixtures across UK and European leagues, prices
1X2 / Over 2.5 / BTTS markets with a lightweight goals model, and flags **value bets**
(positive edge vs the best available bookmaker odds) with fractional-Kelly stake sizing.

## Setup

```bash
pip install -r requirements.txt
```

Provide an [API-Football](https://www.api-football.com) key (free tier works) via an
environment variable **or** Streamlit secrets — it is never hard-coded:

```bash
export API_SPORTS_KEY="your_key_here"
```

or `.streamlit/secrets.toml`:

```toml
API_SPORTS_KEY = "your_key_here"
```

## Run

```bash
streamlit run app.py
```

Set your bankroll, minimum edge %, and Kelly fraction, optionally narrow the leagues /
season / days under **Scan settings**, then **Run Scan**. Results are sorted by edge.

## How it works

- **match_model** — 1X2 probabilities from goal-difference form (clamped).
- **goal_model** — Over 2.5 (logistic on expected goals) and BTTS.
- **extract_best** — best decimal odds per market across bookmakers (line shopping).
- **edge / kelly** — expected value % and fractional-Kelly stake.

API responses are cached (`st.cache_data`) to respect free-tier rate limits, and every
request is timeout-guarded and failure-tolerant.

## Notes / limitations

- Models are intentionally simple (form/goal-difference, no xG or injuries) — treat
  outputs as a research signal, not a guarantee.
- Standings drive the model, so a fixture is only scored when both teams appear in the
  selected season's table.

## Disclaimer

Analytical and research tool only. Betting carries financial risk; past performance is
not indicative of future results. Validate on your own data before staking real money.
