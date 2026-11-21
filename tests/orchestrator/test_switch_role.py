"""A leaf's role from the GUI: leaf <-> border-leaf, a pair together, never with segments."""

import asyncio
from pathlib import Path

import pytest
import yaml

from orchestrator.routing import Routing
from orchestrator.routing_intent import DeviceRouting, RoutingStore
from orchestrator.segments import SegmentError, Segments
from orchestrator.switch_role import change_role
from seed.model import parse
from tests.helpers import seed
from tests.seed.fake_netbox import FakeNetBox

EXAMPLE = Path(__file__).resolve().parents[2] / "seed" / "fabrics" / "example.yaml"
SITE = "example"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def nb() -> FakeNetBox:
    netbox = FakeNetBox()
    run(seed(netbox, parse(yaml.safe_load(EXAMPLE.read_text()))))
    return netbox


def roles(nb, *names):
    return [run(nb.find_one("dcim/devices", name=n))["role"]["slug"] for n in names]


def test_a_pair_changes_role_together_and_back(nb):
    assert run(change_role(nb, "leaf3", "border-leaf")) == {"devices": ["leaf3", "leaf4"], "role": "border-leaf", "changed": True}
    assert roles(nb, "leaf3", "leaf4", "leaf1") == ["border-leaf", "border-leaf", "leaf"]
    assert run(RoutingStore(nb).borders(SITE)) == {"leaf3", "leaf4"}
    run(change_role(nb, "leaf4", "leaf"))
    assert roles(nb, "leaf3", "leaf4") == ["leaf", "leaf"]


def test_reseeding_keeps_the_guis_role(nb):
    run(change_role(nb, "leaf3", "border-leaf"))
    run(seed(nb, parse(yaml.safe_load(EXAMPLE.read_text()))))
    assert roles(nb, "leaf3", "leaf4") == ["border-leaf", "border-leaf"]


def test_refused_with_segments_on_either_switch_and_for_spines(nb):
    segments = Segments(nb)
    run(segments.create(SITE, "web", 10500, 500))
    run(segments.attach(SITE, "leaf4", "10GE1/0/3", 10500, None))
    with pytest.raises(SegmentError, match="leaf4 10GE1/0/3 carries segments"):
        run(change_role(nb, "leaf3", "border-leaf"))
    assert roles(nb, "leaf3", "leaf4") == ["leaf", "leaf"]
    with pytest.raises(SegmentError, match="only leaves change role"):
        run(change_role(nb, "spine1", "border-leaf"))
    with pytest.raises(SegmentError, match="leaf or border-leaf"):
        run(change_role(nb, "leaf3", "spine"))


def test_refused_back_to_leaf_while_routing_intent_needs_the_border_vpn(nb):
    run(change_role(nb, "leaf3", "border-leaf"))
    run(Segments(nb).create(SITE, "db", 10600, 600))
    run(Routing(nb).set_routing(SITE, 10600, True, firewall=None))   # attached nowhere: on the border pair only
    run(RoutingStore(nb).set_device(SITE, "leaf3+leaf4", DeviceRouting.model_validate(
        {"static_routes": [{"vpn": "db-600", "prefix": "10.9.0.0/16", "next_hop": "NULL0"}]})))
    with pytest.raises(SegmentError, match="leaf3 would lose VPN instance db-600"):
        run(change_role(nb, "leaf3", "leaf"))
