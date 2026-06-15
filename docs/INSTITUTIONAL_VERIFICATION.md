# Institutional verification (Brier + Murphy + data room)

Shared metrics for **football** (`hibs-bet` / FVE) and **racing** (`hibs-racing`). External
“institutional grade” requires an out-of-sample data room export with **n ≥ 1,000** settled
events and model Brier **≤ market** on the same window.

## Metrics layer (`metrics/`)

| Module | Role |
|--------|------|
| `metrics/brier.py` | Macro multiclass Brier; per-race `(1/R) Σ(fᵢ−oᵢ)²` for racing |
| `metrics/murphy.py` | Reliability − Resolution + Uncertainty decomposition |
| `metrics/calibration.py` | Top-pick bins (legacy UI) + **all-legs** institutional bins |
| `metrics/data_room.py` | Shared export schema + pass/fail gates |
| `metrics/racing.py` | **Win vs place** targets, venue mapping, market benchmark |

Football code should use `backtest.py` (facade) or import `metrics` directly.

## Football — 1X2

```python
import backtest as bt

export = bt.export_data_room(records, min_events=1000, oos_declared=True)
# export["gates"]["institutional_grade"] → True/False
```

CLI:

```bash
python scripts/export_data_room.py --records records.json --output data_room_football.json
```

Records shape:

```json
{"probs": {"Home": 0.5, "Draw": 0.25, "Away": 0.25}, "outcome": "Home", "market_probs": {...}}
```

`run_backtest.py --data-room` writes the same artifact after a live run.

## Racing — win **or** place (not interchangeable)

| Target | oᵢ vector | Product |
|--------|-----------|---------|
| `win` | exactly one 1 | win-ranker verification |
| `place` | top-k placers = 1 | **hibs-racing place picker** |

### Wire LightGBM ranker → JSONL

After ``fetch-cards --score`` (or settlement), emit one line per settled race:

**From feature_store (batch — no hibs-racing code change):**

```bash
python scripts/emit_racing_verification_jsonl.py \
  --feature-store ~/hibs-racing/data/feature_store.sqlite \
  --output data/verification/settled_races.jsonl \
  --target place --verify
```

Reads `score` / `place_prob` + `finish_position` + `win_decimal` from SQLite
(typically `upcoming_runners` after results land).

**In-process (hibs-racing):** copy `integrations/hibs_racing/settled_race_hook.py` into
hibs-racing and call `emit_race_from_scored_runners()` when a race settles.

**Automated (recommended — cron-safe):**

```bash
# After daily refresh (:05) — run at :20 UTC
FVE_METRICS_ROOT=/opt/football-app HIBS_RACING_DEPLOY_PATH=/opt/hibs-racing \
  bash scripts/racing_verification_automation.sh --run

# Install 06:20 / 12:20 / 17:20 UTC
sudo bash scripts/racing_verification_automation.sh --install-cron
```

Pipeline: **flock** → emit new races → trim window (2500 max) → verify → `data_room_racing.json` + `automation_state.json`. Thin window (&lt;1000 races) exits **0** (`accumulating`); hard fail only if DB missing.

### Cron, sudo, and environment paths

The pipeline **does not need root** to read SQLite or rewrite JSONL under `/opt/hibs-racing/data/`. Root/`sudo` appears only for **`--install-cron`**, which writes the **www-data** crontab (same pattern as hibs-bet `cron-hibs-racing-daily.sh`).

Paths and secrets live in one file — copy `deploy/racing-verification.cron.env.example` to `/opt/hibs-racing/config/verification.cron.env`. The shell wrapper sources it before `--run`; the installed cron line is `bash -lc 'set -a; [ -f …/verification.cron.env ] && . …; set +a; …/racing_verification_automation.sh --run'`, so the crontab itself has **no inline env vars** and no permission fight over `/var/log`.

Default log path is `${HIBS_RACING_DEPLOY_PATH}/logs/verification-automation.log` (www-data writable). Use `/var/log/hibs-racing/` only if that directory is pre-chowned to www-data.

`FVE_METRICS_ROOT` points at the **football-app** checkout that hosts `metrics/` and the venv — not a root requirement.

### SQLite write isolation (feature_store flock + single batch transaction)

| Mechanism | Role |
|-----------|------|
| **`feature_store.sqlite.lock`** | POSIX `flock` shared by verification settlement and hibs-racing daily refresh (`metrics/feature_store_lock.py`, `scripts/feature_store_write_guard.sh`) |
| **`BEGIN IMMEDIATE`** | `apply_results_batch()` holds one write transaction for the entire API payload — failure rolls back all races |
| **WAL + `busy_timeout=30000`** | Readers (`mode=ro` extract) overlap writers; writers wait up to 30s on `SQLITE_BUSY` |
| **`.verification.lock`** | Separate flock — dedupes concurrent **verification** runs only |

Wrap hibs-racing card ingest:

```bash
HIBS_RACING_FEATURE_STORE=/opt/hibs-racing/data/feature_store.sqlite \
  bash /opt/football-app/scripts/feature_store_write_guard.sh \
    python -m hibs_racing.daily_refresh --score
```

If the feature_store flock cannot be acquired within `RACING_FEATURE_STORE_LOCK_WAIT_SEC` (default 60), verification settlement exits **0** with `run_outcome: feature_store_busy` (metrics preserved).

### Schema migrations (replaces runtime `ALTER TABLE`)

Settlement no longer runs ad-hoc DDL. Migrations are versioned in `metrics/feature_store_migrations.py` with a ledger table:

| Component | Detail |
|-----------|--------|
| Ledger | `schema_migrations(version, name, checksum, applied_at)` |
| Head | `001_settlement_columns` — `finish_position` + model score column |
| Apply | One `BEGIN IMMEDIATE` per migration; failure rolls back ledger insert |
| Drift guard | Re-applied checksum must match registry or `MigrationDriftError` |
| Ops CLI | `python scripts/migrate_feature_store.py --feature-store …` |
| Rollback | Forward-only (SQLite); restore backup or ship `002+` corrective migration |

`_prepare_writer()` calls `apply_feature_store_migrations()` before settlement `BEGIN IMMEDIATE`, so schema changes are **outside** the per-batch data transaction.

See `migrations/feature_store/README.md`.

### flock skip vs successful run (`automation_state.json`)

Concurrent runs use non-blocking flock. A skip exits **0** (benign for cron) but must **not** be treated as a fresh verification.

| Field | Successful run | flock skip |
|-------|----------------|------------|
| `run_outcome` | `completed` | `skipped_concurrent` or `feature_store_busy` |
| `skipped` | `false` | `true` |
| `locked` | `false` | `true` |
| `last_full_run_at` | updated to this run | **unchanged** (prior full run) |
| `window` / `emit` / `gates` | refreshed | **carried forward** from last full run |

Alerting: treat `run_outcome == "completed"` (and optionally `last_full_run_at` within SLA) as a true metrics refresh; ignore exit code alone when `skipped` is true.

### Trim window (2500 races) and Murphy resolution

Trim is a **race-count cap**, not a fixed calendar window: keep the last 2,500 settled races by JSONL append order. Calendar span depends on ingest rate (UK/Irish cards ≈ 30–50 settled races/day once the pipeline is warm → **~50–80 calendar days** at cap). If JSONL rows include `race_date` / `settled_at`, `automation_state.json` → `window` reports `oldest_race_date`, `newest_race_date`, and `calendar_days_span`.

Institutional gate requires **≥1,000 races** (macro Brier). Murphy pools **runner legs** across those races (~8 runners/race → **~20k legs** at cap vs **~8k** at the gate floor). Resolution uses 10 probability bins; with place target base rate ~0.25–0.35, 8k+ legs yields stable bin counts for institutional comparison — the 2,500-race cap is headroom above the gate, not the minimum for Murphy.

See `deploy/cron-racing-verification.snippet.sh` for hibs-bet `cron-hibs-racing-daily.sh` hook.

Macro Brier per race:

\[
BS_{\text{race}} = \frac{1}{R}\sum_{i=1}^{R}(f_i - o_i)^2
\]

Aggregate: **mean over races** (macro), not pooled micro-average across all runners.

CLI:

```bash
python scripts/verify_racing_window.py --input races.jsonl --min-races 1000
```

JSONL fields: `target`, `venue_mapped`, `runners[].model_prob`, `runners[].market_prob`,
`won` / `placed`.

**Venue mapping gate:** `mapped_pct ≥ 95%` for institutional grade (unmapped venues listed in export).

## Data room schema (v1.0)

Both products emit:

```json
{
  "schema_version": "1.0",
  "product": "football|racing",
  "target": {"kind": "1x2|win|place"},
  "window": {"min_events": 1000, "n_events": ..., "oos_only": true},
  "model": { "brier_score or macro_brier_per_race", "murphy", "calibration_all_legs" },
  "market": { ... },
  "delta_vs_market": { "brier_score|macro_brier_per_race", "verdict" },
  "gates": {
    "institutional_grade": false,
    "valuation_tier": "internal_engineering|institutional_grade",
    "reasons": []
  }
}
```

Racing adds `venue_mapping`.

## Valuation tiers

| Tier | Meaning |
|------|---------|
| `internal_engineering` | Code + pipeline value; gates not met |
| `institutional_grade` | OOS window, n≥1000, model ≤ market Brier, venue map OK (racing) |

## Promotion ladder (racing)

1. **Shadow** — replay historical cards; export only  
2. **Paper** — live preds, no stakes; rolling 1k gate  
3. **Micro** — place bets only if `gates.institutional_grade`  

## Coexistence

- Football in-sample backtest (`run_backtest.py` on current table) is **not** OOS — use `--in-sample` only for sanity; gates correctly fail.
- FVE Racing Shop tab is odds comparison only — verification belongs in `hibs-racing` via this schema.
