"""Inject shared HIBS product toggle bar into hibs-racing HTML responses."""

from __future__ import annotations


def product_switcher_html(*, active: str = "racing") -> str:
    """Same-origin links: / · /racing/cards · /harvested-execution."""
    active = active if active in ("football", "racing", "trading") else "racing"
    pills = [
        ("football", "/", "⚽ Football"),
        ("racing", "/racing/cards", "🏇 Racing"),
        ("trading", "/harvested-execution", "📈 Trading"),
    ]
    pill_html = []
    for key, href, label in pills:
        cls = "hibs-product-pill active" if key == active else "hibs-product-pill"
        if key in ("racing", "trading"):
            pill_html.append(
                f'<a href="{href}" class="{cls}" rel="noopener">{label}</a>'
            )
        else:
            pill_html.append(f'<a href="{href}" class="{cls}">{label}</a>')
    pills_joined = "\n".join(pill_html)
    return f"""
<style id="hibs-product-switcher-inject">
.hibs-product-bar-inject{{
    display:flex;flex-wrap:wrap;align-items:center;gap:10px 14px;
    padding:8px 14px;margin:0 0 12px;
    background:rgba(6,14,24,0.92);border:1px solid rgba(148,163,184,0.22);border-radius:10px;
    font-family:system-ui,-apple-system,sans-serif;
}}
.hibs-product-switch-inject{{display:inline-flex;border:1px solid rgba(148,163,184,0.25);border-radius:999px;overflow:hidden;}}
.hibs-product-pill{{padding:6px 14px;font-size:0.82em;font-weight:700;text-decoration:none;color:#cbd5e1;background:rgba(0,0,0,0.25);}}
.hibs-product-pill:hover{{background:rgba(0,122,51,0.2);color:#e2e8f0;}}
.hibs-product-pill.active{{background:linear-gradient(135deg,#007A33,#055a28);color:#fff;}}
.hibs-product-label{{font-size:0.75em;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#94a3b8;}}
/* Hide legacy hibs-racing Football|Racing bar when unified 3-pill inject is present */
body:has(#hibs-product-bar-inject) .hibs-product-bar{{display:none !important;}}
</style>
<nav class="hibs-product-bar-inject" role="navigation" aria-label="HIBS products" id="hibs-product-bar-inject">
  <span class="hibs-product-label">HIBS</span>
  <div class="hibs-product-switch-inject">
{pills_joined}
  </div>
</nav>
"""
