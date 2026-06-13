"""Production guards for hibs-racing on VPS (single gunicorn worker).

- Rewrites nav + API paths for /racing subpath (menu/insights fix).
- Blocks in-process UI refresh (fetch-cards in web worker → ping timeout).
- Hides 'Refresh 24h' controls in HTML.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from flask import Flask, Response, jsonify, request

_AUTO_MSG = (
    "Cards update automatically at 06:05, 12:05, and 17:05 UTC. "
    "Manual refresh is disabled on production to keep the site responsive."
)

_BLOCK_PATH_RE = re.compile(
    r"(?:^|/)(?:api/)?(?:refresh|fetch[-_]?cards|cards/refresh|daily[-_]?refresh)",
    re.I,
)

_NAV_FIX_JS: str | None = None


def _ui_refresh_disabled() -> bool:
    return os.getenv("HIBS_DISABLE_UI_REFRESH", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _production_subpath() -> bool:
    return bool((os.getenv("HIBS_URL_PREFIX") or "").strip()) or os.getenv(
        "HIBS_PRODUCTION", ""
    ).strip().lower() in ("1", "true", "yes", "on")


def _nav_fix_js() -> str:
    global _NAV_FIX_JS
    if _NAV_FIX_JS is not None:
        return _NAV_FIX_JS
    js_path = Path(__file__).with_name("racing_nav_prefix_fix.js")
    if js_path.is_file():
        _NAV_FIX_JS = js_path.read_text(encoding="utf-8")
    else:
        _NAV_FIX_JS = ""
    return _NAV_FIX_JS


def apply_production_guards(app: Flask) -> None:
    """Register before/after hooks — idempotent (safe to call once)."""
    if getattr(app, "_hibs_production_guards", False):
        return
    app._hibs_production_guards = True  # type: ignore[attr-defined]

    @app.before_request
    def _hibs_block_heavy_ui_refresh():  # noqa: ANN001
        if not _ui_refresh_disabled():
            return None
        path = (request.path or "").strip()
        if not path:
            return None
        for prefix in ("/racing", os.getenv("HIBS_URL_PREFIX", "")):
            if prefix and path.startswith(prefix):
                path = path[len(prefix) :] or "/"
        if not _BLOCK_PATH_RE.search(path.lstrip("/")):
            return None
        if path.rstrip("/") in ("/cards", "/") and request.method == "GET":
            return None
        if request.method in ("POST", "PUT", "PATCH", "DELETE") or (
            request.method == "GET" and "refresh" in path.lower()
        ):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "ui_refresh_disabled",
                        "message": _AUTO_MSG,
                    }
                ),
                503,
            )
        return None

    @app.after_request
    def _hibs_production_html(resp: Response):  # noqa: ANN001
        ctype = (resp.content_type or "").lower()
        if "text/html" not in ctype:
            return resp
        try:
            body = resp.get_data(as_text=True)
        except Exception:
            return resp

        inject_parts: list[str] = []
        body_changed = False

        if _production_subpath() and "hibs-product-bar-inject" not in body:
            try:
                from product_switcher_inject import product_switcher_html

                bar = product_switcher_html(active="racing")
                if re.search(r"<body[\s>]", body, flags=re.I):
                    body = re.sub(r"(<body[^>]*>)", r"\1" + bar, body, count=1, flags=re.I)
                    body_changed = True
                else:
                    inject_parts.append(bar)
            except Exception:
                pass

        if _production_subpath() and "hibs-racing-nav-fix" not in body:
            nav_js = _nav_fix_js()
            if nav_js:
                inject_parts.append(
                    f'<script id="hibs-racing-nav-fix">{nav_js}</script>'
                )

        if _ui_refresh_disabled() and "refresh" in body.lower():
            if "hibs-auto-refresh-notice" not in body:
                inject_parts.append(
                    """
<style id="hibs-hide-refresh">
button[id*="refresh" i], .btn-refresh, [data-action*="refresh" i],
a[href*="refresh" i] { display: none !important; }
</style>
"""
                )
                inject_parts.append(
                    f'<div id="hibs-auto-refresh-notice" role="status" style="margin:12px 0;'
                    f"padding:10px 14px;border-radius:8px;background:rgba(0,122,51,0.12);"
                    f'border:1px solid rgba(0,122,51,0.35);font-size:0.9em;color:#a7f3d0;">'
                    f"{_AUTO_MSG}</div>"
                )
                inject_parts.append(
                    """
<script id="hibs-strip-refresh">
(function(){
  var re=/refresh\\s*24\\s*h|refresh\\s*24h/i;
  document.querySelectorAll('button,a,[role="button"]').forEach(function(el){
    if(re.test((el.textContent||'').trim())) el.remove();
  });
})();
</script>
"""
                )

        if inject_parts:
            inject = "".join(inject_parts)
            if "</body>" in body:
                body = body.replace("</body>", inject + "</body>", 1)
            else:
                body += inject
            body_changed = True

        if body_changed:
            resp.set_data(body)
        return resp
