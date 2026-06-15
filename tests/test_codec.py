"""Tests for orjson/json/msgpack codec."""

from __future__ import annotations

from pipeline.codec import codec_name, dumps, loads


def test_codec_roundtrip():
    obj = {"fixture_key": "A v B", "odds": 2.1, "nested": [1, 2, 3]}
    assert loads(dumps(obj)) == obj


def test_codec_name_is_known():
    assert codec_name() in ("orjson", "json", "msgpack")


def test_msgpack_pack_unpack():
    import msgpack

    obj = {"type": "line_update", "changed_markets": {"Home": {"soft": {"odds": 2.1}}}}
    raw = msgpack.packb(obj, use_bin_type=True, default=str)
    assert msgpack.unpackb(raw, raw=False) == obj
