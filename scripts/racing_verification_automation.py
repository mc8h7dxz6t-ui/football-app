#!/usr/bin/env python3
"""Robust racing verification automation (cron-safe).

Idempotent pipeline:
  flock → read feature_store.sqlite → append new settled races → trim window
  → evaluate institutional gates → write data_room_racing.json + state

Usage:
  python scripts/racing_verification_automation.py --run
  python scripts/racing_verification_automation.py --install-cron
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from metrics.racing_automation import (
    exit_code_for_report,
    resolve_automation_config,
    run_racing_verification_pipeline,
)

MARKER = "# hibs-racing-verification-automation"


def install_cron(*, metrics_root: Path, log_file: Path) -> None:
    """Install 3× daily cron (after racing card refresh at :05)."""
    import subprocess

    script = metrics_root / "scripts" / "racing_verification_automation.sh"
    if not script.is_file():
        script = metrics_root / "scripts" / "racing_verification_automation.py"
    lines = [
        f"20 6 * * * {script} --run >> {log_file} 2>&1 {MARKER}",
        f"20 12 * * * {script} --run >> {log_file} 2>&1 {MARKER}",
        f"20 17 * * * {script} --run >> {log_file} 2>&1 {MARKER}",
    ]
    log_file.parent.mkdir(parents=True, exist_ok=True)
    existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    body = existing.stdout if existing.returncode == 0 else ""
    filtered = "\n".join(ln for ln in body.splitlines() if MARKER not in ln and "racing_verification_automation" not in ln)
    new_crontab = filtered.rstrip() + "\n\n" + "\n".join(lines) + "\n"
    proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr or "crontab install failed")
    print(f"Installed racing verification cron → {log_file}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Racing verification automation")
    ap.add_argument("--run", action="store_true", help="run pipeline once")
    ap.add_argument("--install-cron", action="store_true")
    ap.add_argument("--feature-store", default="")
    ap.add_argument("--jsonl", default="")
    ap.add_argument("--racing-root", default="")
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--settle-from", default="", help="JSON file of race results to write before emit")
    ap.add_argument("--json", action="store_true", help="print full report JSON to stdout")
    args = ap.parse_args()

    metrics_root = Path(os.environ.get("FVE_METRICS_ROOT", ROOT))

    if args.install_cron:
        log = Path(
            os.environ.get("RACING_VERIFICATION_LOG", "/var/log/hibs-racing/verification-automation.log")
        )
        install_cron(metrics_root=metrics_root, log_file=log)
        return

    if not args.run:
        ap.print_help()
        raise SystemExit(0)

    if args.settle_from:
        os.environ["RACING_RESULTS_JSON"] = args.settle_from

    cfg = resolve_automation_config(
        feature_store=args.feature_store,
        jsonl_path=args.jsonl,
        racing_root=args.racing_root,
        metrics_root=str(metrics_root),
    )
    report = run_racing_verification_pipeline(cfg, use_lock=not args.no_lock, wait_lock=args.wait_lock)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            json.dumps(
                {
                    "status": report.get("status"),
                    "ok": report.get("ok"),
                    "n_races": (report.get("window") or {}).get("n_races"),
                    "institutional_grade": report.get("institutional_grade"),
                    "emit": report.get("emit"),
                    "data_room": report.get("data_room"),
                },
                indent=2,
            )
        )
    raise SystemExit(exit_code_for_report(report))


if __name__ == "__main__":
    main()
