"""Feed registry upstream mode."""

from __future__ import annotations

import feeds.registry as registry_mod


def test_default_registry_includes_book_feeds(monkeypatch):
    monkeypatch.delenv("FVE_UPSTREAM_MODE", raising=False)
    monkeypatch.delenv("HIBS_UPSTREAM_BASE_URL", raising=False)
    reg = registry_mod.build_default_registry()
    names = {f.name for f in reg.enabled()}
    assert "api-football" in names
    assert "matchbook" in names
    assert "hibs-upstream" not in names


def test_upstream_mode_uses_hibs_feed_only(monkeypatch):
    monkeypatch.setenv("FVE_UPSTREAM_MODE", "hibs")
    reg = registry_mod.build_default_registry()
    names = [f.name for f in reg.enabled()]
    assert names == ["hibs-upstream"]
