"""Port protection: Off by default; a template per server port or trunk (an M-LAG trunk on both
switches); never a fabric link, trunk member, the peer-link, MEth or a firewall's port; `owned` stays
once the fabric left Off, so turning it Off removes the platform's lines."""

import asyncio

import pytest

from orchestrator import protection, rollback
from orchestrator.routing import Routing
from orchestrator.segments import SegmentError
from tests.orchestrator.test_dhcp_relay import example_with_web


def run(coro):
    return asyncio.run(coro)


def test_off_by_default_and_owned_once_it_left_off():
    nb = example_with_web(pool=False)
    assert run(protection.document(nb, "example")) == {"mode": "off", "ports": {}}
    run(protection.set_mode(nb, "example", "watch"))
    run(protection.set_mode(nb, "example", "off"))
    assert run(protection.document(nb, "example")) == {"mode": "off", "ports": {}, "owned": True}
    with pytest.raises(SegmentError, match="off, watch, on"):
        run(protection.set_mode(nb, "example", "loud"))


def test_an_mlag_trunk_is_protected_on_both_switches_and_removed_from_both():
    nb = example_with_web(pool=False)
    assert run(protection.set_port(nb, "leaf1", "Eth-Trunk1", "server"))["switches"] == ["leaf1", "leaf2"]
    run(protection.set_port(nb, "leaf1", "10GE1/0/3", "hypervisor"))
    assert run(protection.document(nb, "example"))["ports"] == {
        "leaf1": {"Eth-Trunk1": "server", "10GE1/0/3": "hypervisor"}, "leaf2": {"Eth-Trunk1": "server"}}
    run(protection.set_port(nb, "leaf2", "Eth-Trunk1", None))
    assert run(protection.document(nb, "example"))["ports"] == {"leaf1": {"10GE1/0/3": "hypervisor"}}
    assert run(protection.get_port(nb, "leaf1", "10GE1/0/3"))["template"] == "hypervisor"


@pytest.mark.parametrize("port, reason", [
    ("10GE1/0/1", "trunk member"),
    ("100GE1/0/1", "fabric link"),
    ("Eth-Trunk0", "peer-link"),
    ("MEth0/0/0", "only front-panel ports"),
    ("10GE1/0/4", "faces a firewall or ISP"),
])
def test_refusals(port, reason):
    nb = example_with_web(pool=False)
    run(Routing(nb).create_service("example", "FW", "leaf1", "10GE1/0/4", 3000, "10.255.0.1/30", "10.255.0.2", 65100))
    with pytest.raises(SegmentError, match=reason):
        run(protection.set_port(nb, "leaf1", port, "server"))
    with pytest.raises(SegmentError, match="template 'loud'"):
        run(protection.set_port(nb, "leaf1", "10GE1/0/3", "loud"))


def test_rollback_puts_the_document_back_and_keeps_owned():
    now = {"fabric": {"protection": {"mode": "on", "owned": True, "ports": {"leaf1": {"10GE1/0/3": "server"}}}}}
    then = {"fabric": {}}
    ops = rollback.plan(now, then, rollback.Scope(devices=None))["operations"]
    assert [(o["kind"], o["document"]) for o in ops] == [("protection", {"mode": "off", "ports": {}})]
    nb = example_with_web(pool=False)
    run(protection.set_mode(nb, "example", "on"))
    run(protection.restore(nb, "example", {"mode": "off", "ports": {}}))
    assert run(protection.document(nb, "example")) == {"mode": "off", "ports": {}, "owned": True}


def test_tripped_ports_come_from_the_collector_series_in_plain_words():
    """the collector's series per (switch, port), with the seconds before it reopens."""
    from orchestrator.prometheus import PrometheusClient

    class Prom(PrometheusClient):
        def __init__(self):
            super().__init__("http://prometheus")

        async def query(self, expr):
            if expr.startswith("fabric_port_protection_tripped{"):
                return [({"device": "leaf1", "interface": "10GE1/0/10", "cause": "loopback-detect",
                          "state": "error-down", "detail": ""}, 0.0, 1.0),
                        ({"device": "leaf1", "interface": "10GE1/0/1", "cause": "storm-control",
                          "state": "block", "detail": "broadcast"}, 0.0, 1.0)]
            if expr.startswith("fabric_port_protection_reopen_seconds{"):
                return [({"device": "leaf1", "interface": "10GE1/0/10", "cause": "loopback-detect"}, 0.0, 31.0)]
            return []

    found = run(protection.tripped(Prom(), ["leaf1"]))
    assert found[("leaf1", "10GE1/0/10")] == [{"cause": "loopback-detect", "state": "error-down", "detail": "", "reopen": 31}]
    assert protection.trip_text("10GE1/0/1", found[("leaf1", "10GE1/0/1")][0]) == \
        "10GE1/0/1: storm control (broadcast), blocking the excess"
    assert run(protection.tripped(None, ["leaf1"])) == {}
