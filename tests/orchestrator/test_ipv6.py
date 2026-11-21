"""IPv6 for routed segments: the fabric pool and its relay /64, a segment's /64 and
::1 gateway, its hosts mode and DHCPv6 servers relayed through the relay loopback's /128."""

import asyncio

import pytest

from orchestrator import dhcp_relay, ipv6
from orchestrator.routing import Routing
from orchestrator.segments import SegmentError, Segments
from tests.collector.test_collector import example_netbox
from tests.orchestrator.test_dhcp_relay import example_with_web, published, relay_loopbacks

POOL = "fd12:3456:789a::/48"


def run(coro):
    return asyncio.run(coro)


def dual_stack(**kw):
    nb = example_with_web()
    run(ipv6.set_pool(nb, "example", POOL))
    return nb


def test_the_pool_keeps_its_first_64_for_relay_loopbacks_and_segments_take_the_next():
    nb = dual_stack()
    pool, relay = run(ipv6.prefixes(nb, "example"))
    assert (pool["prefix"], relay["prefix"]) == (POOL, "fd12:3456:789a::/64")
    assert run(ipv6.set_ipv6(nb, "example", 10500, True))["ipv6"] == {"hosts": "slaac", "servers": []}
    assert published(nb)["ipv6"] == {"gateway": "fd12:3456:789a:1::1/64", "hosts": "slaac", "servers": []}


def test_dhcpv6_servers_put_a_128_on_the_relay_loopback_and_off_takes_it_away():
    nb = dual_stack()
    run(ipv6.set_ipv6(nb, "example", 10500, True, "dhcpv6", [{"address": "fd12:3456:789a:1::53"}]))
    assert relay_loopbacks(nb) == {("leaf1", "LoopBack2"): ["fd12:3456:789a::1/128"]}   # IPv6 alone: no IPv4 relay
    routing = published(nb)
    assert routing["relay"]["loopbacks6"] == {"leaf1": "fd12:3456:789a::1/128"} and routing["relay"]["servers"] == []

    run(dhcp_relay.set_relay(nb, "example", 10500, [{"address": "192.0.2.10"}]))        # IPv4 relay too: same loopback
    assert sorted(relay_loopbacks(nb)[("leaf1", "LoopBack2")]) == ["10.10.8.1/32", "fd12:3456:789a::1/128"]
    run(dhcp_relay.set_relay(nb, "example", 10500, []))                                    # IPv4 off: DHCPv6 keeps it
    assert published(nb)["relay"]["loopback"] == 2
    run(ipv6.set_ipv6(nb, "example", 10500, True, "slaac"))                                # DHCPv6 off: loopback gone
    assert relay_loopbacks(nb) == {} and "relay" not in published(nb)


def test_ipv6_off_and_unrouting_release_the_64():
    nb = dual_stack()
    run(ipv6.set_ipv6(nb, "example", 10500, True))
    run(ipv6.set_ipv6(nb, "example", 10500, False))
    assert "ipv6" not in published(nb)
    run(ipv6.set_ipv6(nb, "example", 10500, True))
    run(Routing(nb).set_routing("example", 10500, False))
    segment = next(s for s in run(Segments(nb).all("example")) if s["vni"] == 10500)
    assert segment["routing"] is None
    assert not [p for p in run(nb.find("ipam/prefixes")) if (p.get("description") or "").endswith(" IPv6 gateway")]


@pytest.mark.parametrize("hosts, servers, message", [
    ("stateless", [], "hosts is one of"),
    ("slaac", [{"address": "fd12:3456:789a:1::53"}], "need the hosts mode dhcpv6"),
    ("dhcpv6", [{"address": "10.0.0.1"}], "DHCPv6 server"),
    ("dhcpv6", [{"address": "fe80::1"}], "global or ULA"),
    ("dhcpv6", [{"address": "fd00::53", "service": "FW"}], "not this segment's firewall with IPv6"),
])
def test_invalid_ipv6_settings_are_refused(hosts, servers, message):
    with pytest.raises(SegmentError, match=message):
        run(ipv6.set_ipv6(dual_stack(), "example", 10500, True, hosts, servers))


def test_ipv6_needs_a_routed_segment_and_the_pool():
    nb = example_with_web()
    with pytest.raises(SegmentError, match="IPv6 pool first"):
        run(ipv6.set_ipv6(nb, "example", 10500, True))
    run(ipv6.set_pool(nb, "example", POOL))
    run(Segments(nb).create("example", "app", 10700, 700))
    with pytest.raises(SegmentError, match="not routed"):
        run(ipv6.set_ipv6(nb, "example", 10700, True))
    with pytest.raises(SegmentError, match="a /32 to a /56"):
        run(ipv6.set_pool(example_netbox(), "example", "fd00::/64"))
    run(ipv6.set_ipv6(nb, "example", 10500, True))
    with pytest.raises(SegmentError, match="turn their IPv6 off first"):
        run(ipv6.set_pool(nb, "example", "fd99::/48"))


def with_fw():
    """The dual-stack example with firewall FW on leaf1 as web's firewall."""
    nb = dual_stack()
    run(Routing(nb).create_service("example", "FW", "leaf1", "10GE1/0/4", 3000, "10.255.0.1/30", "10.255.0.2", 65100))
    run(Routing(nb).set_routing("example", 10500, True, firewall="FW"))
    return nb


def fw(nb):
    return next(s for s in run(Routing(nb).services("example")) if s["name"] == "FW")


def test_a_service_gets_ipv6_from_a_subnet_and_dhcpv6_may_then_sit_behind_it():
    nb = with_fw()
    run(Routing(nb).set_service_ipv6("example", "FW", {"address": "fd00:255::/64", "next_hop": "fd00:255::2"}))
    assert fw(nb)["ipv6"] == {"address": "fd00:255::1/64", "next_hop": "fd00:255::2"}
    run(ipv6.set_ipv6(nb, "example", 10500, True, "dhcpv6", [{"address": "fd00:200::5", "service": "FW"}]))
    with pytest.raises(SegmentError, match="sit behind FW"):
        run(Routing(nb).set_service_ipv6("example", "FW", None))
    with pytest.raises(SegmentError, match="sit behind FW"):
        run(Routing(nb).set_routing("example", 10500, True, firewall=None))
    run(ipv6.set_ipv6(nb, "example", 10500, True))
    run(Routing(nb).set_service_ipv6("example", "FW", None))
    assert fw(nb)["ipv6"] is None


@pytest.mark.parametrize("address, next_hop, message", [
    ("fd00:255::/48", "fd00:255::2", "/64 to a /126"),
    ("fd00:255::1/64", "fd00:255::1", "another host"),
    ("fd00:255::/64", "fd00:256::2", "another host"),
    ("fe80::/64", "fe80::2", "ULA"),
    ("fd12:3456:789a:ff::/64", "fd12:3456:789a:ff::2", "inside the fabric's IPv6 pool"),
])
def test_invalid_service_ipv6_is_refused(address, next_hop, message):
    with pytest.raises(SegmentError, match=message):
        run(Routing(with_fw()).set_service_ipv6("example", "FW", {"address": address, "next_hop": next_hop}))


def test_a_pair_service_takes_ipv6_from_a_subnet_or_typed_addresses():
    nb = dual_stack()
    routing = Routing(nb)
    made = run(routing.create_service("example", "PFW", "leaf1", "Eth-Trunk1", 3005, "10.255.5.0/29", "10.255.5.6",
                                      ipv6={"address": "fd00:5::/64", "next_hop": "fd00:5::6"}))
    assert made["ipv6"] == {"address": "fd00:5::3/64", "next_hop": "fd00:5::6",
                            "addresses": {"leaf1": "fd00:5::1/64", "leaf2": "fd00:5::2/64"}}
    typed = {"address": "fd00:5::a/64", "next_hop": "fd00:5::6",
             "addresses": {"leaf1": "fd00:5::b/64", "leaf2": "fd00:5::c/64"}}
    assert run(routing.set_service_ipv6("example", "PFW", typed))["ipv6"] == typed
    for bad, message in (({**typed, "addresses": {"leaf1": "fd00:5::b/64"}}, "one address for each"),
                         ({**typed, "next_hop": "fd00:5::b"}, "another host"),
                         ({"address": "fd00:5::a/64", "next_hop": "fd00:5::6"}, "give the Vlanif subnet")):
        with pytest.raises(SegmentError, match=message):
            run(routing.set_service_ipv6("example", "PFW", bad))
    with pytest.raises(SegmentError, match="for a service on an M-LAG pair"):
        run(Routing(with_fw()).set_service_ipv6("example", "FW", typed))


def test_ipv6_routing_intent_checks_families_and_ipv6_vpn_instances():
    from orchestrator.routing_intent import DeviceRouting, RoutingStore

    nb = with_fw()
    store = RoutingStore(nb)
    for bad, message in (
        ({"prefix_lists": {"MIX": [{"index": 10, "action": "permit", "prefix": "10.0.0.0/8"},
                                   {"index": 20, "action": "permit", "prefix": "fd00::/8"}]}}, "mix"),
        ({"static_routes": [{"prefix": "fd00:1::/48", "next_hop": "10.0.0.1"}]}, "another address family"),
        ({"prefix_lists": {"V4": [{"index": 10, "action": "permit", "prefix": "10.0.0.0/8"}]},
          "bgp_peers": [{"address": "fd00:99::2", "asn": 65300, "export_prefix_list": "V4"}]}, "is IPv4"),
    ):
        with pytest.raises(ValueError, match=message):
            DeviceRouting.model_validate(bad).check_references()
    v6_in_fw = DeviceRouting.model_validate({"static_routes": [{"vpn": "FW", "prefix": "fd00:1::/48", "next_hop": "NULL0"}]})
    with pytest.raises(SegmentError, match="FW has no IPv6"):
        run(store.set_device("example", "leaf1+leaf2", v6_in_fw))
    run(Routing(nb).set_service_ipv6("example", "FW", {"address": "fd00:255::/64", "next_hop": "fd00:255::2"}))
    run(store.set_device("example", "leaf1+leaf2", v6_in_fw))
    with pytest.raises(SegmentError, match="would remove the IPv6 of VPN instance FW"):
        run(Routing(nb).set_service_ipv6("example", "FW", None))
