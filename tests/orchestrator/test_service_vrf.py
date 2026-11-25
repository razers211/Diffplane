"""A new service in an existing VPN instance: another service's or a routed
segment's, and what that refuses."""

import pytest

from orchestrator.routing import Routing
from orchestrator.segments import SegmentError, Segments
from tests.orchestrator.test_routing import context, nb, run  # noqa: F401  (fixture)

FW = dict(device="leaf1", port="10GE1/0/10", vlan=3001, address="10.255.0.1/30", next_hop="10.255.0.2", asn=65100)
ISP = dict(device="leaf1", port="10GE1/0/11", vlan=3002, address="10.255.2.1/30", next_hop="10.255.2.2", kind="isp")
INSIDE = dict(device="leaf1", port="10GE1/0/12", vlan=3004, address="10.255.4.1/30", next_hop="10.255.4.2")


def routed_web(nb):  # noqa: F811
    run(Segments(nb).create("example", "web", 10500, 500))
    run(Routing(nb).set_routing("example", 10500, True, firewall=None))


def by_name(nb):  # noqa: F811
    return {s["name"]: s for s in run(Routing(nb).services("example"))}


def test_joining_another_services_instance_keeps_its_rd_after_it_goes(nb):  # noqa: F811
    r = Routing(nb)
    run(r.create_service("example", "FW", **FW))
    run(r.create_service("example", "ISP", vrf="FW", **ISP))
    assert (by_name(nb)["ISP"]["vrf"], by_name(nb)["ISP"]["rd_vlan"]) == ("FW", 3001)
    assert context(nb)["services"]["ISP"]["vrf"] == "FW" and "vrf" not in context(nb)["services"]["FW"]

    run(r.delete_service("example", "FW"))
    assert by_name(nb)["ISP"]["vrf"] == "FW" and by_name(nb)["ISP"]["rd_vlan"] == 3001
    with pytest.raises(SegmentError, match="route distinguisher"):   # FW's VLAN is still the instance's RD
        run(r.create_service("example", "OTHER", **{**FW, "port": "10GE1/0/13"}))
    with pytest.raises(SegmentError, match="route distinguishers would clash"):
        run(Segments(nb).create("example", "seg", 13001, 3001))


def test_another_switch_joins_an_instance_unless_its_rd_vlan_is_taken_there(nb):  # noqa: F811
    r = Routing(nb)
    run(r.create_service("example", "FW", **FW))
    run(r.create_service("example", "THERE", **{**INSIDE, "device": "leaf2", "vlan": FW["vlan"]}))
    with pytest.raises(SegmentError, match="uses VLAN 3001, which service THERE already uses on leaf2"):
        run(r.create_service("example", "ISP", vrf="FW", **{**ISP, "device": "leaf2"}))
    run(r.delete_service("example", "THERE"))
    run(r.create_service("example", "ISP", vrf="FW", **{**ISP, "device": "leaf2"}))
    isp = by_name(nb)["ISP"]
    assert (isp["vrf"], isp["rd_vlan"], isp["devices"]) == ("FW", 3001, ["leaf2"])
    with pytest.raises(SegmentError, match="no service or routed segment"):
        run(r.create_service("example", "ISP2", vrf="NOPE", **{**ISP, "port": "10GE1/0/13"}))


def test_a_firewall_in_a_segments_instance_is_that_segments_alone(nb):  # noqa: F811
    r = Routing(nb)
    routed_web(nb)
    run(r.create_service("example", "INSIDE", vrf="web-500", **INSIDE))
    assert by_name(nb)["INSIDE"]["vrf"] == "web-500" and by_name(nb)["INSIDE"]["rd_vlan"] is None
    run(r.set_routing("example", 10500, True, firewall="INSIDE"))   # its own segment: allowed

    run(Segments(nb).create("example", "db", 10600, 600))
    with pytest.raises(SegmentError, match="can only be that segment's firewall"):
        run(r.set_routing("example", 10600, True, firewall="INSIDE"))
    run(r.set_routing("example", 10500, True, firewall=None))
    with pytest.raises(SegmentError, match="sits in segment web's VPN instance"):
        run(r.set_routing("example", 10500, False))
    with pytest.raises(SegmentError, match="sits in segment web's VPN instance"):
        run(Segments(nb).rename("example", 10500, "front"))
    with pytest.raises(SegmentError, match="has no IPv6"):
        run(r.set_service_ipv6("example", "INSIDE", {"address": "fd00:1::/64", "next_hop": "fd00:1::2"}))

    run(r.delete_service("example", "INSIDE"))
    run(r.set_routing("example", 10500, False))   # nothing in it any more


def test_a_centralized_segment_instance_has_no_firewall_session(nb, monkeypatch):  # noqa: F811
    from orchestrator.routing_intent import RoutingStore

    routed_web(nb)

    async def gateways(self, fabric):   # the example fabric as a centralized fabric for this test
        return {"leaf1", "leaf2"}

    monkeypatch.setattr(RoutingStore, "gateways", gateways)
    with pytest.raises(SegmentError, match="no BGP session"):
        run(Routing(nb).create_service("example", "INSIDE", vrf="web-500", asn=65200, **INSIDE))
