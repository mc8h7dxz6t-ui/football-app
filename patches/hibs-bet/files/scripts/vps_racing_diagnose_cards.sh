#!/usr/bin/env bash
# VPS-only: why /racing/cards is empty (run on server as root).
#   sudo bash /opt/hibs-bet/scripts/vps_racing_diagnose_cards.sh
set -euo pipefail

APP="${HIBS_RACING_DEPLOY_PATH:-/opt/hibs-racing}"
BET="${HIBS_BET_DEPLOY_PATH:-/opt/hibs-bet}"

echo "==> hibs-racing service"
systemctl is-active hibs-racing 2>/dev/null || echo inactive
echo

echo "==> raceform"
ls -lh "${APP}/data/raceform.db" 2>/dev/null || echo "MISSING ${APP}/data/raceform.db"
grep RACEFORM "${APP}/.env" 2>/dev/null || echo "RACEFORM_DB_PATH not in .env"
echo

echo "==> sqlite in ${APP}/data"
ls -lh "${APP}/data/" 2>/dev/null || true
du -sh "${APP}/data" 2>/dev/null || true
echo

echo "==> row counts (cards/meetings)"
if [[ -x "${APP}/.venv/bin/python" ]]; then
  sudo -u www-data env HOME="${APP}" PYTHONPATH=src "${APP}/.venv/bin/python" <<'PY'
import sqlite3
from pathlib import Path
data = Path("/opt/hibs-racing/data")
found = False
for p in sorted(data.glob("*.sqlite")):
    print(f"--- {p.name} ({p.stat().st_size} bytes)")
    con = sqlite3.connect(p)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1"
    )]
    for tbl in tables:
        if any(k in tbl.lower() for k in ("card", "meet", "race", "runner")):
            try:
                n = con.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()[0]
                print(f"  {tbl}: {n}")
                found = found or (n > 0)
            except Exception as e:
                print(f"  {tbl}: err {e}")
    con.close()
if not found:
    print("NO CARD ROWS in any sqlite — UI will be empty")
print()
print("==> form column (last 5-6 runs)")
for col in ("form", "form_line", "form_str", "last_runs", "recent_form"):
    try:
        con = sqlite3.connect(data / "feature_store.sqlite")
        up_cols = {r[1] for r in con.execute("PRAGMA table_info(upcoming_runners)")}
        if col not in up_cols:
            con.close()
            continue
        n = con.execute("SELECT COUNT(*) FROM upcoming_runners").fetchone()[0]
        f = con.execute(
            f"SELECT COUNT(*) FROM upcoming_runners WHERE [{col}] IS NOT NULL AND TRIM([{col}]) != ''"
        ).fetchone()[0]
        print(f"  upcoming_runners.{col}: {f}/{n}")
        if f:
            sample = con.execute(
                f"SELECT horse_name, [{col}] FROM upcoming_runners "
                f"WHERE [{col}] IS NOT NULL AND TRIM([{col}]) != '' LIMIT 1"
            ).fetchone()
            if sample:
                print(f"    sample: {sample[0]} -> {sample[1]}")
        con.close()
    except Exception:
        pass
PY
fi
echo

echo "==> last daily refresh log"
tail -30 /var/log/hibs-racing/daily-refresh.log 2>/dev/null || echo "(no log)"
echo

echo "==> cards HTML snippet"
html="$(curl -sS --max-time 60 http://127.0.0.1:5003/cards 2>/dev/null || true)"
if echo "${html}" | grep -qi 'no card in db'; then
  echo "EMPTY: page contains 'No card in DB'"
elif echo "${html}" | grep -qi 'card-row\|runner\|meeting'; then
  echo "OK: page looks populated"
else
  echo "UNKNOWN: check browser — shell HTML only in snippet"
fi
echo

echo "==> UI message check"
curl -sS --max-time 60 http://127.0.0.1:5003/cards 2>/dev/null | grep -i 'no card in db' && \
  echo "UI: No card in DB for next 24h — fetch day 0 AND day 1, then rsync" || true
echo

echo "==> fix paths"
echo "A) Mac (recommended):"
echo "   cd ~/hibs-racing && source .venv/bin/activate"
echo "   hibs-racing fetch-cards --source racing_api --day 0 --score"
echo "   hibs-racing fetch-cards --source racing_api --day 1 --score"
echo "   cd ~/hibs-betting-app && ./scripts/deploy_racing_data_to_vps.sh"
echo "B) VPS (needs RACING_API keys in ${APP}/.env):"
echo "   sudo bash ${BET}/scripts/vps_racing_fix_cards.sh"
