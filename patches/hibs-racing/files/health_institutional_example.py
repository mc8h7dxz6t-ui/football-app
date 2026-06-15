"""Example institutional fields for hibs-racing GET /api/health.

Copy into hibs-racing health handler — not executed by FVE.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict


def institutional_health_extension(
    *,
    coverage_pct: float | None = None,
    recon_clean: bool | None = None,
    paper_rows: int | None = None,
    cron_scheduled: bool | None = None,
    last_run_utc: str | None = None,
) -> Dict[str, Any]:
    """Merge into existing /api/health JSON."""
    out: Dict[str, Any] = {}
    if coverage_pct is not None:
        out["telemetry_balance"] = {
            "coverage_pct": round(float(coverage_pct), 2),
            "matchbook_share_pct": None,
        }
    if recon_clean is not None:
        out["recon_clean"] = bool(recon_clean)
    if paper_rows is not None:
        out["paper"] = {"n_rows": int(paper_rows)}
    if cron_scheduled is not None or last_run_utc:
        out["cron"] = {
            "scheduled": bool(cron_scheduled) if cron_scheduled is not None else None,
            "last_run_utc": last_run_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    deploy = (os.environ.get("HIBS_EVIDENCE_DEPLOY_DATE") or "").strip()
    if deploy:
        out["evidence_deploy_date"] = deploy
    return out
