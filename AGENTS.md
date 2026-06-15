# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

Single-process **Python / Streamlit** app (`app.py`) — no Docker, no database, no monorepo. Core modules: `model.py`, `backtest.py`, `xg_sources.py`, `run_backtest.py`.

### PATH

`pip install --user` puts CLI tools in `~/.local/bin`. Add to PATH before running commands:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Use `python3` (not `python`) for scripts like `run_backtest.py`.

### Install dependencies

See `README.md`. Typical dev setup:

```bash
pip install -r requirements-dev.txt   # app + pytest
pip install -r requirements-xg.txt    # optional Understat xG (heavy)
```

### Run / test

| Task | Command |
|------|---------|
| Unit tests | `pytest -q` |
| Streamlit UI | `streamlit run app.py` → http://localhost:8501 |
| Headless engine demo (no API) | `python3 run_backtest.py --simulate 6000` |
| Live scan/backtest | Requires `API_SPORTS_KEY` (or `API_FOOTBALL_KEY`) env var or `.streamlit/secrets.toml` |

### API key

Live fixture/odds data needs a free [API-Football](https://www.api-football.com) key. Optional [The Odds API](https://the-odds-api.com) key (`ODDS_API_KEY`) widens book coverage and powers the **Racing Shop** tab. Copy `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` to configure locally.

Line shopping lives in `odds_shopping.py` + `bookmakers.py`; multi-source merge in `odds_sources.py`.

### Inst++ stack (optional)

| Component | Command / path |
|-----------|----------------|
| Pro deps | `pip install -r requirements-pro.txt` |
| API | `uvicorn api.main:app --port 8000` |
| Hands-off stack | `cp .env.example .env` → `bash scripts/run_stack.sh` (Redis + API + auto worker + UI) |
| Arb shadow (frozen) | `docker compose --profile arb-shadow up -d` — see `docs/ARB_FREEZE.md` |
| Ingest worker | `python worker.py --auto` (discover fixtures) or `--fixtures 'key:id:matchbook_id'` |
| Preflight | `bash scripts/preflight_fve.sh` — budgets + optional line cache check |
| CI | GitHub Actions `FVE CI` — `pytest tests/` + optional `scripts/ci_fve_api_smoke.sh` |
| Exchange poll override | `FEED_POLL_SEC_MATCHBOOK=0.5` |
| Intra-window peaks | Redis **ZSET** rings per market; `PEAK_ODDS_WINDOW_SEC=5` |
| Async scheduler | Default in `worker.py`; `--sync` for blocking loop |
| Celery (optional) | `celery -A tasks.celery_app worker -B` |
| Redis | `docker compose up -d redis` — set `REDIS_URL` |
| Matchbook | `MATCHBOOK_USERNAME` + `MATCHBOOK_PASSWORD` |

Streamlit should use `FVE_API_URL=http://localhost:8000` when the ingest layer is running.

### WebSocket line hub + API budgets

| Item | Detail |
|------|--------|
| WS endpoint | `ws://localhost:8000/ws/lines/{fixture_key}` — snapshot on connect, `line_update` on tick change |
| Delta merge | `FVE_WS_CLIENT_DELTA=0` (default) hub sends full `lines`; set `1` for raw `changed_markets` |
| Cross-process | Worker + API must share `REDIS_URL` (line_bus pub/sub) |
| Odds API feed | `ENABLE_ODDS_API_FEED=1` only when quota allows; default poll 300s |
| Shared quotas | `FVE_MATCHBOOK_MAX_CALLS_PER_HOUR`, `FVE_ODDS_API_MAX_CALLS_PER_HOUR` (default **15**), `FVE_API_FOOTBALL_MAX_CALLS_PER_HOUR` |
| Health | `GET /health` → `api_budgets`, `line_bus` |

Architecture roadmap (Institutional++ vs what we actually need): `docs/ARCHITECTURE.md`.

### Linting

No linter is configured in this repo. Validation is via `pytest -q`.

### Long-running services

Start Streamlit in tmux for background use:

```bash
streamlit run app.py --server.headless true --server.port 8501
```

Default port: **8501**.
