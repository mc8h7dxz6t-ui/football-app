from pipeline.watchlist import parse_fixture_spec


def test_parse_fixture_spec():
    keys, ctx = parse_fixture_spec("Arsenal v Chelsea:99:123")
    assert keys == ["Arsenal v Chelsea"]
    assert ctx["Arsenal v Chelsea"]["fixture_id"] == 99
    assert ctx["Arsenal v Chelsea"]["matchbook_event_id"] == 123
    assert ctx["Arsenal v Chelsea"]["home_team"] == "Arsenal"


def test_parse_fixture_spec_empty():
    keys, ctx = parse_fixture_spec("")
    assert keys == []
    assert ctx == {}
