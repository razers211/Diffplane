"""DHCP relay on segment gateways: the setting, its pool and the per-leaf relay loopbacks
kept in step with the segment's ports."""

import asyncio

import pytest

from orchestrator import dhcp_relay
from orchestrator.routing import Routing
from orchestrator.segments import SegmentError, Segments
from tests.collector.test_collector import example_netbox


def run(coro):
    return asyncio.run(coro)


def example_with_web(pool: bool = True):
    nb = example_netbox()
    run(Segments(nb).create("example", "web", 10500, 500))
    run(Segments(nb).attach("example", "leaf1", "10GE1/0/3", 10500, None))
    run(Routing(nb).set_routing("example", 10500, True, firewall=None))
    if pool:
        run(dhcp_relay.set_pool(nb, "example", "10.10.8.0/24"))
    return nb


def relay_loopbacks(nb) -> dict[tuple[str, str], list[str]]:
    found = {}
    for i in run(nb.find("dcim/interfaces", site="example")):
        if (i.get("description") or "").startswith(dhcp_relay.MARK):
            ips = [a["address"] for a in run(nb.find("ipam/ip-addresses", interface_id=i["id"]))]
            found[(i["device"]["name"], i["name"])] = ips
    return found


def published(nb) -> dict:
    context = run(nb.find_one("extras/config-contexts", name="segments-example"))
    return context["data"]["segments"]["10500"]["routing"]


def test_relay_on_gives_each_gateway_leaf_a_loopback_from_the_pool_and_off_removes_it():
    nb = example_with_web()
    result = run(dhcp_relay.set_relay(nb, "example", 10500, [{"address": "192.0.2.10"}]))
    assert result["relay"] == {"servers": [{"address": "192.0.2.10"}], "loopback": 2}
    assert relay_loopbacks(nb) == {("leaf1", "LoopBack2"): ["10.10.8.1/32"]}
    assert published(nb)["relay"] == {"servers": [{"address": "192.0.2.10"}], "loopback": 2,
                                      "loopbacks": {"leaf1": "10.10.8.1/32"}}   # for the firewall's leaf

    run(dhcp_relay.set_relay(nb, "example", 10500, []))
    assert relay_loopbacks(nb) == {} and "relay" not in published(nb)


def test_the_loopbacks_follow_the_segment_s_ports_and_the_number_is_kept():
    nb = example_with_web()
    run(dhcp_relay.set_relay(nb, "example", 10500, [{"address": "192.0.2.10"}]))
    run(Segments(nb).detach("example", "leaf1", "10GE1/0/3", 10500))
    assert relay_loopbacks(nb) == {}                                   # no port left: no gateway there
    run(Segments(nb).attach("example", "leaf1", "10GE1/0/4", 10500, None))
    assert list(relay_loopbacks(nb)) == [("leaf1", "LoopBack2")]
    run(dhcp_relay.set_relay(nb, "example", 10500, [{"address": "192.0.2.11"}]))
    assert published(nb)["relay"]["loopback"] == 2                     # assigned once, kept


def test_a_second_segment_takes_the_next_free_number():
    nb = example_with_web()
    run(Segments(nb).create("example", "db", 10600, 600))
    run(Segments(nb).attach("example", "leaf1", "10GE1/0/6", 10600, None))
    run(Routing(nb).set_routing("example", 10600, True, firewall=None, peers=[10500]))
    run(dhcp_relay.set_relay(nb, "example", 10500, [{"address": "192.0.2.10"}]))
    db = run(dhcp_relay.set_relay(nb, "example", 10600, [{"address": "192.0.2.10", "segment": 10500}]))
    assert db["relay"]["loopback"] == 3 and db["relay"]["servers"] == [{"address": "192.0.2.10", "segment": 10500}]
    assert sorted(relay_loopbacks(nb)) == [("leaf1", "LoopBack2"), ("leaf1", "LoopBack3")]


@pytest.mark.parametrize("servers, message", [
    ([{"address": "dhcp.example.com"}], "DHCP server"),
    ([{"address": "224.0.0.1"}], "unicast"),
    ([{"address": "192.0.2.10"}, {"address": "192.0.2.10"}], "listed twice"),
    ([{"address": f"192.0.2.{i}"} for i in range(1, 10)], "at most 8"),
    ([{"address": "192.0.2.10", "service": "NOPE"}], "not this segment's firewall"),
    ([{"address": "192.0.2.10", "segment": 10700}], "not a segment this one is peered with"),
])
def test_invalid_servers_are_refused(servers, message):
    with pytest.raises(SegmentError, match=message):
        run(dhcp_relay.set_relay(example_with_web(), "example", 10500, servers))


def test_relay_needs_a_routed_segment_and_a_pool():
    nb = example_with_web(pool=False)
    with pytest.raises(SegmentError, match="relay loopback pool"):
        run(dhcp_relay.set_relay(nb, "example", 10500, [{"address": "192.0.2.10"}]))
    run(Segments(nb).create("example", "app", 10700, 700))
    with pytest.raises(SegmentError, match="not routed"):
        run(dhcp_relay.set_relay(nb, "example", 10700, [{"address": "192.0.2.10"}]))


def test_the_pool_may_not_overlap_and_stays_while_loopbacks_use_it():
    nb = example_with_web()
    with pytest.raises(SegmentError, match="overlaps"):
        run(dhcp_relay.set_pool(nb, "example", "10.10.0.0/24"))           # the LoopBack0 pool
    run(dhcp_relay.set_relay(nb, "example", 10500, [{"address": "192.0.2.10"}]))
    with pytest.raises(SegmentError, match="turn relay off"):
        run(dhcp_relay.set_pool(nb, "example", "10.10.9.0/24"))
    assert run(dhcp_relay.set_pool(nb, "example", "10.10.8.0/24")) == {"prefix": "10.10.8.0/24"}   # unchanged: fine


def test_the_api_sets_the_pool_and_the_relay(monkeypatch):
    from fastapi.testclient import TestClient

    from orchestrator import app as app_module
    from orchestrator.app import app, get_netbox

    async def unfrozen():
        return None

    nb = example_with_web(pool=False)
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[app_module._intent_unfrozen] = unfrozen
    app.dependency_overrides[app_module.get_accounts] = lambda: type("A", (), {"sessions": None})()
    monkeypatch.setattr("orchestrator.deps.audit", lambda *_args, **_kwargs: asyncio.sleep(0))
    try:
        client = TestClient(app, base_url="https://testserver")
        assert client.put("/fabrics/example/relay-pool", json={"prefix": "10.10.8.0/24"}).status_code == 200
        assert client.get("/fabrics/example").json()["relay_pool"] == "10.10.8.0/24"
        body = client.put("/segments/example/10500/relay", json={"servers": [{"address": "192.0.2.10"}]}).json()
        assert body["relay"] == {"servers": [{"address": "192.0.2.10"}], "loopback": 2}
        assert client.put("/segments/example/10500/relay", json={"servers": [{"address": "x"}]}).status_code == 400
    finally:
        for dep in (get_netbox, app_module._intent_unfrozen, app_module.get_accounts):
            app.dependency_overrides.pop(dep, None)
