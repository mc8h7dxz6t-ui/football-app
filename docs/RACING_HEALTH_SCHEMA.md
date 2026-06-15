# hibs-racing `/api/health` institutional schema (parity with football F7–F9)

hibs-bet probes this via `racing_evidence.py` for gates **R5–R7**.

## Required fields (Phase 2 evidence surface)

```json
{
  "status": "ok",
  "revision": "abc123",
  "telemetry_balance": {
    "coverage_pct": 42.1,
    "matchbook_share_pct": 18.0
  },
  "recon_clean": true,
  "paper": {
    "n_rows": 120
  },
  "cron": {
    "scheduled": true,
    "last_run_utc": "2026-06-11T06:00:00Z"
  }
}
```

## Gate mapping

| Gate | Field path | Pass threshold |
|------|------------|----------------|
| R5 coverage | `telemetry_balance.coverage_pct` | ≥50% prod, ≥35% obs |
| R6 recon | `recon_clean` | `true` |
| R7 paper | `paper.n_rows` | ≥25 |

`racing_evidence.py` also accepts legacy paths: `telemetry.coverage_pct`, `paper_rows`, `institutional.recon_clean`.

## Deploy checklist (hibs-racing repo)

1. Add fields to `/api/health` handler (see `patches/hibs-racing/files/health_institutional_example.py`).
2. Set `HIBS_EVIDENCE_DEPLOY_DATE` in racing `.env` for since-deploy metrics.
3. Run `daily_refresh.sh` + `institutional-check --require-recon-clean` on cron.
4. Verify from hibs-bet: `./scripts/verify_racing_evidence_gates.sh`.

## Football unified stack probe

Football `/api/health` may include `stack_ops.racing` when wired — optional cross-product dashboard.
