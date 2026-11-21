"""Microsegmentation intent: every routed segment an EPG with an ID kept for good, named
groups inside segments, contracts with their stored precedence, and the per-chip limits."""

import asyncio

import pytest

from orchestrator import microseg
from orchestrator.routing import Routing
from orchestrator.segments import SegmentError, Segments
from tests.orchestrator.test_dhcp_relay import example_with_web


def run(coro):
    return asyncio.run(coro)


def example():
    """web (routed, 10.100.0.0/24) and db (routed, peered with web) in the example fabric."""
    nb = example_with_web(pool=False)
    run(Segments(nb).create("example", "db", 10600, 600))
    run(Routing(nb).set_routing("example", 10600, True, firewall=None, peers=[10500]))
    return nb


def epgs(nb) -> dict[str, dict]:
    return {e["name"]: e for e in run(microseg.get(nb, "example"))["epgs"]}


def test_nothing_is_numbered_until_it_is_set_up_then_every_routed_segment_has_an_epg():
    nb = example()
    assert run(microseg.get(nb, "example"))["epgs"] == []
    run(microseg.set_mode(nb, "example", "learn"))
    found = epgs(nb)
    assert (found["web"]["id"], found["db"]["id"]) == (1, 2)
    assert found["web"]["prefixes"] == ["10.100.0.0/24"] and found["web"]["inside"] == "permit"

    run(Segments(nb).create("example", "app", 10700, 700))
    run(Routing(nb).set_routing("example", 10700, True, firewall=None))   # a segment publish numbers it
    assert epgs(nb)["app"]["id"] == 3


def test_an_unrouted_segment_keeps_its_id_inactive():
    nb = example()
    run(microseg.set_mode(nb, "example", "enforce"))
    run(Routing(nb).set_routing("example", 10600, False))
    db = next(e for e in run(microseg.get(nb, "example"))["epgs"] if e["segment"] == 10600)
    assert (db["id"], db["active"], db["name"]) == (2, False, None)


def test_a_group_lives_inside_its_segment():
    nb = example()
    run(microseg.set_mode(nb, "example", "learn"))
    group = run(microseg.set_group(nb, "example", 10500, "admins", ["10.100.0.16/28"], ["02:00:FC:00:00:01"], "deny"))
    assert group == {"id": 3, "segment": 10500, "name": "admins", "prefixes": ["10.100.0.16/28"],
                     "macs": ["0200-fc00-0001"], "inside": "deny"}
    assert epgs(nb)["web-admins"]["inside"] == "deny"
    with pytest.raises(SegmentError, match="not inside segment web"):
        run(microseg.set_group(nb, "example", 10500, "ops", ["10.100.1.0/28"], []))
    with pytest.raises(SegmentError, match="at least one prefix or MAC"):
        run(microseg.set_group(nb, "example", 10500, "ops", [], []))
    with pytest.raises(SegmentError, match="exists"):
        run(microseg.set_group(nb, "example", 10500, "admins", ["10.100.0.32/28"], []))
    with pytest.raises(SegmentError, match="longer than 31"):
        run(microseg.set_group(nb, "example", 10500, "a" * 28, ["10.100.0.32/28"], []))


def test_a_member_belongs_to_one_epg_only():
    """V300 refuses a prefix already in another EPG (leaf1 deploy)."""
    nb = example()
    run(microseg.set_mode(nb, "example", "learn"))
    with pytest.raises(SegmentError, match="whole subnet"):
        run(microseg.set_group(nb, "example", 10500, "all", ["10.100.0.0/24"], []))
    run(microseg.set_group(nb, "example", 10500, "admins", ["10.100.0.16/28"], ["0200-fc00-0001"]))
    with pytest.raises(SegmentError, match="already in EPG 3"):
        run(microseg.set_group(nb, "example", 10500, "ops", ["10.100.0.16/28"], []))
    with pytest.raises(SegmentError, match="0200-fc00-0001 already in EPG 3"):
        run(microseg.set_group(nb, "example", 10500, "ops", ["10.100.0.32/28"], ["0200-fc00-0001"]))


def test_contracts_keep_their_precedence_and_count_their_reply_rules():
    nb = example()
    run(microseg.set_mode(nb, "example", "learn"))
    web, db = epgs(nb)["web"]["id"], epgs(nb)["db"]["id"]
    first = run(microseg.set_contract(nb, "example", web, db, [{"protocol": "tcp", "ports": [5432]}]))
    second = run(microseg.set_contract(nb, "example", db, web, [{"protocol": "icmp"}]))
    assert (first["precedence"], second["precedence"]) == (1, 2)
    assert first["services"] == [{"protocol": "tcp", "ports": [5432, 5432]}]
    replaced = run(microseg.set_contract(nb, "example", web, db, [{"protocol": "udp", "ports": [53]}, {"protocol": "any"}]))
    assert replaced["precedence"] == 1
    assert run(microseg.get(nb, "example"))["usage"]["rules"] == 6   # (2 services + 1) x forward and reply, IPv4 only

    with pytest.raises(SegmentError, match="inside behaviour"):
        run(microseg.set_contract(nb, "example", web, web, [{"protocol": "any"}]))
    with pytest.raises(SegmentError, match="ports are only for tcp or udp"):
        run(microseg.set_contract(nb, "example", web, db, [{"protocol": "icmp", "ports": [1]}]))
    with pytest.raises(SegmentError, match="leave ports out"):
        run(microseg.set_contract(nb, "example", web, db, [{"protocol": "tcp", "ports": [0, 65535]}]))
    with pytest.raises(SegmentError, match="used by 1 contract"):
        group = run(microseg.set_group(nb, "example", 10500, "admins", ["10.100.0.16/28"], []))
        run(microseg.set_contract(nb, "example", group["id"], db, [{"protocol": "any"}]))
        run(microseg.delete_group(nb, "example", group["id"]))
    run(microseg.delete_contract(nb, "example", db, web))
    assert [c["precedence"] for c in run(microseg.get(nb, "example"))["contracts"]] == [1, 3]


def test_members_count_once_per_vpn_instance_that_routes_to_them():
    nb = example()
    run(microseg.set_mode(nb, "example", "learn"))
    # web and db are peered: each segment's subnet is written with both instances
    assert run(microseg.get(nb, "example"))["usage"]["members"] == 4


def test_over_the_switch_limits_is_refused(monkeypatch):
    nb = example()
    run(microseg.set_mode(nb, "example", "learn"))
    monkeypatch.setitem(microseg.LIMITS, "members", 5)
    with pytest.raises(SegmentError, match="members 6 > 5"):
        run(microseg.set_group(nb, "example", 10500, "admins", ["10.100.0.16/28"], []))


def test_it_turns_on_only_where_every_leaf_announces_the_module():
    nb = example()
    run(Routing(nb).set_routing("example", 10600, True, firewall=None, peers=[10500]))
    from orchestrator.lifecycle import Lifecycle

    run(Lifecycle(nb).activate("leaf1", "172.24.0.11/24"))
    assert run(microseg.switches(nb, "example")) == ["leaf1"]   # leaf2 is planned

    async def r023(name):
        return False

    with pytest.raises(SegmentError, match="leaf1 \\(no huawei-microsegmentation: V300R025"):
        run(microseg.check_software(nb, "example", r023))

    async def r025(name):
        return True

    run(microseg.check_software(nb, "example", r025))


def test_the_api_sets_groups_and_contracts_and_checks_the_software(monkeypatch):
    from fastapi.testclient import TestClient

    from orchestrator import app as app_module
    from orchestrator.app import app, get_netbox

    async def unfrozen():
        return None

    class Reader:
        async def has_module(self, name, module):
            return module == "huawei-microsegmentation"

    nb = example()
    overrides = {get_netbox: lambda: nb, app_module._intent_unfrozen: unfrozen, app_module._microseg_reader: Reader,
                 app_module.get_accounts: lambda: type("A", (), {"sessions": None})()}
    app.dependency_overrides.update(overrides)
    monkeypatch.setattr("orchestrator.deps.audit", lambda *_args, **_kwargs: asyncio.sleep(0))
    try:
        client = TestClient(app)
        assert client.put("/fabrics/example/microsegmentation/mode", json={"mode": "learn"}).status_code == 200
        group = client.post("/fabrics/example/microsegmentation/groups",
                            json={"segment": 10500, "name": "admins", "prefixes": ["10.100.0.16/28"]})
        assert group.status_code == 200, group.text
        contract = client.put("/fabrics/example/microsegmentation/contracts",
                              json={"from": group.json()["id"], "to": 2, "services": [{"protocol": "tcp", "ports": [22]}]})
        assert contract.status_code == 200, contract.text
        view = client.get("/fabrics/example/microsegmentation").json()
        assert view["mode"] == "learn" and [e["name"] for e in view["epgs"]] == ["web", "db", "web-admins"]
        assert client.put("/fabrics/example/microsegmentation/mode", json={"mode": "sideways"}).status_code == 400
        assert client.delete(f"/fabrics/example/microsegmentation/groups/{group.json()['id']}").status_code == 400   # in use
        assert client.delete(f"/fabrics/example/microsegmentation/contracts/{group.json()['id']}/2").status_code == 200
    finally:
        for dep in overrides:
            app.dependency_overrides.pop(dep, None)


def test_the_switch_state_is_read_as_tcam_per_chip_contract_counters_and_failures():
    """From the leaf1 probe (V300R025), with counters and one failed rule edited in."""
    from pathlib import Path

    xml = (Path(__file__).parent / "data" / "microseg" / "state.xml").read_text()
    found = microseg.parse_state(xml)
    assert [(c["chip"], c["tcam_block_used"], c["tcam_block_limit"], c["epg_used"], c["gbp_rule_used"]) for c in found["chips"]] == [
        ("0", 2, 4, 3, 4), ("1", 2, 4, 3, 4)]
    assert found["contracts"]["GBP-1-2"]["matched_packets"] == 12 and found["contracts"]["GBP-1-2"]["dropped_packets"] == 3
    assert found["failed_rules"] == [{"classifier": "GBP-1-2", "rule": 40, "state": "fail"}]
    assert found["failed_members"] == []

    class Reader:
        async def get_state(self, name, subtree):
            assert "<resources/>" in subtree and "<classifier-based-statistics/>" in subtree
            return xml

    assert run(microseg.state(Reader(), "leaf1"))["device"] == "leaf1"
