#!/usr/bin/env python3
"""Apply versioned feature_store migrations (ops / bootstrap).

Usage:
  python scripts/migrate_feature_store.py --feature-store /opt/hibs-racing/data/feature_store.sqlite
  python scripts/migrate_feature_store.py --status
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

from metrics.feature_store_lock import feature_store_lock, lock_wait_enabled
from metrics.feature_store_migrations import apply_feature_store_migrations, migration_status
from metrics.racing_settlement import _connect_write
from metrics.racing_sqlite import _choose_source_table


def _resolve_db(arg: str) -> Path:
    if arg:
        return Path(arg)
    env = os.environ.get("HIBS_RACING_FEATURE_STORE", "")
    if env:
        return Path(env)
    return Path.home() / "hibs-racing" / "data" / "feature_store.sqlite"


def main() -> None:
    ap = argparse.ArgumentParser(description="feature_store schema migrations")
    ap.add_argument("--feature-store", default="", help="path to feature_store.sqlite")
    ap.add_argument("--table", default="", help="override source table")
    ap.add_argument("--status", action="store_true", help="print migration ledger only")
    ap.add_argument("--no-lock", action="store_true")
    args = ap.parse_args()

    db = _resolve_db(args.feature_store)
    if not db.is_file():
        raise SystemExit(f"database not found: {db}")

    table_override = args.table or os.environ.get("RACING_VERIFICATION_TABLE") or None

    def _run() -> dict:
        con = _connect_write(db)
        try:
            if args.status:
                return migration_status(con)
            src = _choose_source_table(con, table=table_override)
            return apply_feature_store_migrations(con, src)
        finally:
            con.close()

    if args.no_lock:
        report = _run()
    else:
        with feature_store_lock(db, wait=lock_wait_enabled()):
            report = _run()

    print(json.dumps({"feature_store": str(db), **report}, indent=2))


if __name__ == "__main__":
    main()
