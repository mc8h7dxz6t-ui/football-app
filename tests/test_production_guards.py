"""Tests for verify_production_guards.sh behaviour."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_verify_script_fails_without_guard_in_crontab(tmp_path):
  env = os.environ.copy()
  env["HIBS_RACING_DEPLOY_PATH"] = str(tmp_path / "hr")
  env["CRON_USER"] = env.get("USER", "ubuntu")
  proc = subprocess.run(
    ["bash", str(ROOT / "scripts" / "verify_production_guards.sh")],
    cwd=ROOT,
    env=env,
    capture_output=True,
    text=True,
  )
  assert proc.returncode != 0
  assert "feature_store_write_guard" in proc.stdout + proc.stderr
