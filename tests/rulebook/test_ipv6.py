"""Patches for dual-stack segments, both directions, with our rulebook: the VPN
instance's IPv6 family before the BGP family that needs its RD, and the reverse on removal."""

from pathlib import Path

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

GOLDEN = Path(__file__).parents[1] / "golden"
BEFORE = (GOLDEN / "example-relay" / "leaf1.cfg").read_text()    # IPv4 only
AFTER = (GOLDEN / "example-ipv6" / "leaf1.cfg").read_text()      # the same segments dual-stack


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and line.strip() != "quit"]


def test_ipv6_on_adds_the_family_before_bgp_uses_it():
    result = lines(patch(BEFORE, AFTER))
    assert result.index("ipv6-family") < result.index("ipv6-family vpn-instance web-500")
    assert "ipv6 address FD12:3456:789A:1::1/64" in result and "ipv6 nd ra halt disable" in result
    assert result.index("interface LoopBack3") < result.index("dhcpv6 relay source-interface LoopBack3")


def test_ipv6_off_leaves_out_what_the_family_removal_takes_with_it():
    """V300's `undo ipv6-family` removes the instance's BGP IPv6 family and the IPv6 addresses of
    interfaces bound to it, and keeps `ipv6 enable` and `ipv6 nd` (leaf1)."""
    result = lines(patch(AFTER, BEFORE))
    assert "undo ipv6-family" in result
    assert not any(line.startswith("undo ipv6 address") for line in result)
    assert not any(line.startswith("undo ipv6-family vpn-instance") for line in result)
    for line in ("undo ipv6 enable", "undo ipv6 nd ra halt disable", "undo ipv6 nd collect host enable",
                 "undo dhcpv6 relay source-interface"):
        assert line in result
    assert not any(line.startswith("undo dhcpv6 relay source-interface LoopBack") for line in result)


CENTRAL_BEFORE = (GOLDEN / "example-central-routed" / "c-gw1.cfg").read_text()
CENTRAL_AFTER = (GOLDEN / "example-central-ipv6" / "c-gw1.cfg").read_text()


def test_centralized_gateway_on_and_off():
    """The gateway pair of a centralized fabric: the IPv6 family (RD only) before the VBDIF and
    loopback addresses and the inter-VPN routes that use it; off, the routes go first and the
    family takes the addresses with it."""
    on = lines(patch(CENTRAL_BEFORE, CENTRAL_AFTER))
    route = "ipv6 route-static vpn-instance web-500 FD12:3456:789B:2:: 64 vpn-instance db-600"
    assert on.index("ipv6-family") < on.index("ipv6 address FD12:3456:789B:1::1/64") < on.index(route)
    assert not any("vpn-target" in line for line in on)
    assert not any(line.endswith(("vpn-instance web-500", "vpn-instance db-600")) and "family" in line for line in on)
    assert "m-lag ipv6 address FD00:255::3" in on   # FW on the gateway pair (M-LAG trunk)
    off = lines(patch(CENTRAL_AFTER, CENTRAL_BEFORE))
    assert off.index("undo " + route) < off.index("undo ipv6-family")
    assert not any(line.startswith("undo ipv6 address") for line in off)


SERVICE_BEFORE = AFTER                                                          # example-ipv6: FW IPv4 only
SERVICE_AFTER = (GOLDEN / "example-ipv6-services" / "leaf1.cfg").read_text()    # FW dual-stack


def test_service_ipv6_on_builds_the_family_before_its_users():
    on = lines(patch(SERVICE_BEFORE, SERVICE_AFTER))
    route = "ipv6 route-static vpn-instance web-500 :: 0 vpn-instance FW FD00:255::2"
    assert on.index("ipv6-family") < on.index("ipv6 address FD00:255::1/64") < on.index(route)
    assert on.index(route) < on.index("ipv6-family vpn-instance FW") < on.index("peer FD00:255::2 enable")
    assert "peer FD00:255::2 as-number 65100" in on and "peer FD00:255::2 bfd enable" in on


def test_service_ipv6_off_removes_routes_first_and_leaves_what_the_family_takes():
    off = lines(patch(SERVICE_AFTER, SERVICE_BEFORE))
    route = "undo ipv6 route-static vpn-instance web-500 :: 0 vpn-instance FW FD00:255::2"
    assert off.index(route) < off.index("undo ipv6-family")
    assert "undo ipv6 address FD00:255::1/64" not in off and "undo ipv6-family vpn-instance FW" not in off
    assert "undo ipv6 enable" in off and "undo peer FD00:255::2" in off


ROUTING_BEFORE = SERVICE_AFTER                                                  # dual-stack services, no v6 intent
ROUTING_AFTER = (GOLDEN / "example-routing6" / "leaf1.cfg").read_text()        # IPv6 routing intent


def test_ipv6_routing_intent_on_builds_filters_before_their_users():
    on = lines(patch(ROUTING_BEFORE, ROUTING_AFTER))
    prefix = "ip ipv6-prefix OWN6 index 10 permit FD00:100:: 48 greater-equal 48 less-equal 64"
    assert on.index(prefix) < on.index("route-policy IN6 permit node 10") < on.index("peer FD00:99::2 route-policy IN6 import")
    assert on.index("peer FD00:99::2 as-number 65300") < on.index("ipv6-family unicast") < on.index("peer FD00:99::2 enable")
    assert "if-match ipv6 address prefix-list OWN6" in on and "peer FD00:99::2 ipv6-prefix OWN6 export" in on


def test_ipv6_routing_intent_off_removes_users_before_filters():
    off = lines(patch(ROUTING_AFTER, ROUTING_BEFORE))
    assert "undo ipv6 route-static FD00:300:: 48 NULL0" in off
    assert off.index("undo peer FD00:99::2") < off.index("undo route-policy IN6 node 10") < off.index(
        "undo ip ipv6-prefix OWN6 index 99999999")
    assert "undo peer FD00:255::9" in off and "undo aggregate FD00:400:: 48 detail-suppressed" in off
