"""QoS intent: on or off per fabric, DSCP marks per segment and per port (an M-LAG trunk on
both leaves), refused where V300 takes no traffic policy (a trunk member) or the port is routed."""

import asyncio

import pytest

from orchestrator import qos
from orchestrator.segments import SegmentError
from tests.orchestrator.test_dhcp_relay import example_with_web


def run(coro):
    return asyncio.run(coro)


def test_off_by_default_then_on_with_marks():
    nb = example_with_web(pool=False)
    assert run(qos.get(nb, "example")) == {"enabled": False, "segments": {}, "ports": {}}
    run(qos.set_enabled(nb, "example", True))
    run(qos.set_segment(nb, "example", 10500, "storage"))
    assert run(qos.set_port(nb, "example", "leaf1", "10GE1/0/3", "scavenger"))["switches"] == ["leaf1"]
    assert run(qos.get(nb, "example")) == {"enabled": True, "segments": {"10500": "storage"},
                                       "ports": {"leaf1": {"10GE1/0/3": "scavenger"}}}
    run(qos.set_segment(nb, "example", 10500, None))
    run(qos.set_port(nb, "example", "leaf1", "10GE1/0/3", None))
    assert run(qos.get(nb, "example")) == {"enabled": True, "segments": {}, "ports": {}}


def test_an_mlag_trunk_is_marked_on_both_leaves():
    nb = example_with_web(pool=False)
    assert run(qos.set_port(nb, "example", "leaf1", "Eth-Trunk1", "storage"))["switches"] == ["leaf1", "leaf2"]
    assert set(run(qos.get(nb, "example"))["ports"]) == {"leaf1", "leaf2"}


@pytest.mark.parametrize("device, port, reason", [
    ("leaf1", "10GE1/0/1", "trunk member; mark its trunk"),
    ("leaf1", "100GE1/0/1", "routed fabric link"),
    ("leaf1", "Eth-Trunk0", "peer-link"),
    ("leaf1", "MEth0/0/0", "only physical ports and server trunks take a mark"),
    ("leaf1", "10GE9/9/9", "has no interface"),
    ("nowhere", "10GE1/0/3", "no switch"),
])
def test_ports_that_cannot_take_a_mark(device, port, reason):
    with pytest.raises(SegmentError, match=reason):
        run(qos.set_port(example_with_web(pool=False), "example", device, port, "storage"))


def test_unknown_marks_and_segments_are_refused():
    nb = example_with_web(pool=False)
    with pytest.raises(SegmentError, match="storage or scavenger"):
        run(qos.set_segment(nb, "example", 10500, "gold"))
    with pytest.raises(SegmentError, match="no segment 10999"):
        run(qos.set_segment(nb, "example", 10999, "storage"))


def test_the_api_turns_it_on_and_marks(monkeypatch):
    from fastapi.testclient import TestClient

    from orchestrator import app as app_module
    from orchestrator.app import app, get_netbox

    async def unfrozen():
        return None

    nb = example_with_web(pool=False)
    overrides = {get_netbox: lambda: nb, app_module._intent_unfrozen: unfrozen,
                 app_module.get_accounts: lambda: type("A", (), {"sessions": None})()}
    app.dependency_overrides.update(overrides)
    monkeypatch.setattr("orchestrator.deps.audit", lambda *_args, **_kwargs: asyncio.sleep(0))
    try:
        client = TestClient(app)
        assert client.put("/fabrics/example/qos/enabled", json={"enabled": True}).status_code == 200
        assert client.put("/fabrics/example/qos/segments/10500", json={"mark": "storage"}).status_code == 200
        port = client.put("/fabrics/example/qos/ports", json={"device": "leaf1", "port": "10GE1/0/3", "mark": "scavenger"})
        assert port.status_code == 200, port.text
        assert client.put("/fabrics/example/qos/ports", json={"device": "leaf1", "port": "10GE1/0/1", "mark": "storage"}).status_code == 400
        view = client.get("/fabrics/example/qos").json()
        assert view["enabled"] and view["segments"] == {"10500": "storage"}
        assert view["plan"]["weights"] == {"0": 30, "1": 5, "2": 15, "3": 35, "4": 15}
        assert client.delete("/fabrics/example/qos/segments/10500").status_code == 200
        assert client.put("/fabrics/example/qos/ports", json={"device": "leaf1", "port": "10GE1/0/3", "mark": None}).status_code == 200
        assert client.get("/fabrics/example/qos").json()["ports"] == {}
    finally:
        for dep in overrides:
            app.dependency_overrides.pop(dep, None)
