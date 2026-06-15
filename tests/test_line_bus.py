from pipeline.line_bus import LineBus, channel_for


def test_channel_for_fixture():
    assert channel_for("Arsenal v Chelsea") == "fve:bus:lines:Arsenal v Chelsea"


def test_local_bus_delivers_once():
    bus = LineBus(redis_url="redis://127.0.0.1:59999/0")
    received: list[tuple[str, dict]] = []

    bus.subscribe_local(lambda fk, msg: received.append((fk, msg)))
    bus.publish("x v y", {"type": "update", "fixture_key": "x v y"})
    assert len(received) == 1
    assert received[0][0] == "x v y"
    assert received[0][1]["type"] == "update"
