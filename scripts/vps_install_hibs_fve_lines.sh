#!/usr/bin/env bash
# VPS: expose hibs-bet /api/fve/lines for FVE upstream (no duplicate book ingest).
#
#   curl -fsSL https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/main/scripts/vps_install_hibs_fve_lines.sh | sudo bash
set -euo pipefail

APP="${DEPLOY_PATH:-/opt/hibs-bet}"
BRANCH="${HIBS_FVE_LINES_BRANCH:-main}"
RAW="${HIBS_FVE_LINES_RAW:-https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/${BRANCH}/patches/hibs-bet/files}"

log() { echo "[hibs-fve-lines] $*"; }

[[ -d "${APP}/src/hibs_predictor" ]] || { echo "ERROR: ${APP} not found" >&2; exit 1; }

dest="${APP}/src/hibs_predictor/fve_lines_proxy.py"
static_dest="${APP}/static/fve_ws_lines.js"
mkdir -p "$(dirname "${dest}")" "$(dirname "${static_dest}")"
curl -fsSL "${RAW}/src/hibs_predictor/fve_lines_proxy.py" -o "${dest}"
curl -fsSL "${RAW}/static/fve_ws_lines.js" -o "${static_dest}" 2>/dev/null || true
log "installed fve_lines_proxy.py + fve_ws_lines.js"

if ! grep -q "register_fve_lines_routes" "${APP}/src/hibs_predictor/web.py" 2>/dev/null; then
  python3 <<'PY' "${APP}/src/hibs_predictor/web.py"
import sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
needle = "app = Flask(__name__)"
if needle not in text:
    raise SystemExit("could not find Flask app in web.py")
insert = (
    "\n# FVE upstream lines (read-only, token optional via FVE_LINES_TOKEN)\n"
    "try:\n"
    "    from hibs_predictor.fve_lines_proxy import register_fve_lines_routes\n"
    "    register_fve_lines_routes(app)\n"
except Exception:\n"
    "    pass\n"
)
if "register_fve_lines_routes" not in text:
    text = text.replace(needle, needle + insert, 1)
    path.write_text(text, encoding="utf-8")
    print("wired register_fve_lines_routes in web.py")
else:
    print("web.py already wired")
PY
fi

touch "${APP}/.env"
if ! grep -q '^FVE_LINES_TOKEN=' "${APP}/.env" 2>/dev/null; then
  echo "# Optional shared secret for FVE upstream" >> "${APP}/.env"
  echo "FVE_LINES_TOKEN=" >> "${APP}/.env"
fi

systemctl restart hibs-bet 2>/dev/null || true
log "done — FVE can set:"
echo "  FVE_UPSTREAM_MODE=hibs"
echo "  HIBS_UPSTREAM_BASE_URL=https://hibs-bet.co.uk"
echo "  HIBS_UPSTREAM_TOKEN=<same as FVE_LINES_TOKEN if set>"
