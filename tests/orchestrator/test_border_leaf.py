"""Border leaves of a symmetric fabric in the orchestrator: routing
only, so no segments on their ports; once a fabric has them, new services go there; every routed
segment's VPN instance is on them, so routing intent may use it there."""

import asyncio
from pathlib import Path

import pytest
import yaml

from orchestrator.routing import Routing
from orchestrator.routing_intent import RoutingStore
from orchestrator.segments import SegmentError, Segments
from seed.model import parse
from tests.helpers import seed
from tests.seed.fake_netbox import FakeNetBox

EXAMPLE = Path(__file__).resolve().parents[2] / "seed" / "fabrics" / "example.yaml"
SITE = "example"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def nb() -> FakeNetBox:
    data = yaml.safe_load(EXAMPLE.read_text())
    data["leaf_pairs"][1]["role"] = "border-leaf"   # leaf3 + leaf4
    netbox = FakeNetBox()
    run(seed(netbox, parse(data)))
    return netbox


def test_border_leaf_ports_carry_no_segments(nb):
    segments = Segments(nb)
    run(segments.create(SITE, "web", 10500, 500))
    with pytest.raises(SegmentError, match="leaf3 is a border leaf"):
        run(segments.attach(SITE, "leaf3", "10GE1/0/3", 10500, None))
    with pytest.raises(SegmentError, match="leaf4 is a border leaf"):
        run(segments.attach(SITE, "leaf4", "10GE1/0/3", 10500, 500))
    run(segments.attach(SITE, "leaf1", "Eth-Trunk1", 10500, 500))   # ordinary leaves as before


def test_new_services_go_on_border_leaves(nb):
    routing = Routing(nb)
    with pytest.raises(SegmentError, match="has border leaves .*not on leaf1"):
        run(routing.create_service(SITE, "FW", "leaf1", "10GE1/0/4", 3000, "10.255.0.1/30", "10.255.0.2", 65100))
    run(routing.create_service(SITE, "FW", "leaf3", "10GE1/0/4", 3000, "10.255.0.1/30", "10.255.0.2", 65100))


def test_every_routed_segment_vpn_is_on_the_border_pair(nb):
    segments = Segments(nb)
    run(segments.create(SITE, "web", 10500, 500))
    run(segments.attach(SITE, "leaf1", "Eth-Trunk1", 10500, 500))
    run(segments.create(SITE, "db", 10600, 600))
    routing = Routing(nb)
    run(routing.set_routing(SITE, 10500, True, firewall=None))
    run(routing.set_routing(SITE, 10600, True, firewall=None))
    store = RoutingStore(nb)
    assert run(store.borders(SITE)) == {"leaf3", "leaf4"}
    assert run(store.vpns(SITE, "leaf3+leaf4")) == ["db-600", "web-500"]   # db is attached nowhere
    assert run(store.vpns(SITE, "leaf1+leaf2")) == ["web-500"]              # attached there only


def test_a_fabric_without_border_leaves_is_unchanged():
    netbox = FakeNetBox()
    run(seed(netbox, parse(yaml.safe_load(EXAMPLE.read_text()))))
    assert run(RoutingStore(netbox).borders(SITE)) == set()
    run(Routing(netbox).create_service(SITE, "FW", "leaf1", "10GE1/0/4", 3000, "10.255.0.1/30", "10.255.0.2", 65100))


def test_vlan_trunks_carry_no_segments(nb):
    """An imported VLAN trunk (an imported fabric's Eth-Trunk1) passes VLANs on its main port, and VRP
    refuses a segment's dot1q sub-interface for one of them (rack1-leaf1)."""
    run(nb.create("extras/config-contexts", {"name": f"overrides-{SITE}", "data": {"overrides": {"devices": {
        "leaf1": {"vlan_trunks": {"Eth-Trunk1": {"vlans": "2 to 4094", "undo_vlan1": True}}}}}}}))
    segments = Segments(nb)
    run(segments.create(SITE, "web", 10500, 500))
    with pytest.raises(SegmentError, match="leaf1 Eth-Trunk1 is a VLAN trunk"):
        run(segments.attach(SITE, "leaf1", "Eth-Trunk1", 10500, 500))
    with pytest.raises(SegmentError, match="leaf1 Eth-Trunk1 is a VLAN trunk"):   # from its M-LAG peer too
        run(segments.attach(SITE, "leaf2", "Eth-Trunk1", 10500, None))
    assert "Eth-Trunk1" not in {p["name"] for p in run(segments.ports("leaf1"))}
    assert "Eth-Trunk1" in {p["name"] for p in run(segments.ports("leaf2"))}   # leaf2's own side has no VLAN trunk here
    run(segments.attach(SITE, "leaf1", "10GE1/0/3", 10500, None))   # its other ports as before
