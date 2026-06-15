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
