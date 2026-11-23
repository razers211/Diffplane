"""DC links: two fabrics' border pairs, their uplinks and stretched segments, checked on save;
what a link no longer has is retired until a verified deploy of the switch."""

import asyncio
import copy

import pytest

from orchestrator import dci
from orchestrator.segments import SegmentError, Segments
from tests.generators.test_golden import DCI_SIDES, ROOT, example_dci


def run(coro):
    return asyncio.run(coro)


def linked():
    from seed.model import load
    from tests.helpers import seed
    from tests.seed.fake_netbox import FakeNetBox

    nb = FakeNetBox()
    run(seed(nb, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    run(example_dci(nb))
    return nb


def sides(**change):
    found = copy.deepcopy(DCI_SIDES)
    for key, value in change.items():
        index, field = key.split("_", 1)
        found[int(index)][field] = value
    return found


def test_a_link_derives_each_sides_asn_loopbacks_and_vtep():
    doc = run(dci.get(linked()))
    link = doc["links"][0]
    assert [(s["fabric"], s["asn"], s["vtep"]) for s in link["sides"]] == [("example", 65001, "10.10.1.1"), ("example-pip", 65004, "10.40.2.2")]
    assert link["sides"][1]["loopbacks"] == {"p-leaf1": "10.40.0.3", "p-leaf2": "10.40.0.4"}
    assert link["segments"] == [{"name": "web", "vnis": {"example": 10500, "example-pip": 10500}, "dci_vni": 60000}]
    assert dci.peers_of(doc, "leaf1") == {"10.40.0.3", "10.40.0.4", "10.255.10.2"}


@pytest.mark.parametrize("change, reason", [
    ({"1_fabric": "example"}, "two different fabrics"),
    ({"0_pair": ["leaf1", "spine1"]}, "not an M-LAG pair"),
    ({"0_pair": ["leaf1"]}, "both switches"),
    ({"0_pair": ["leaf1", ""]}, "both switches of the border pair"),
    ({"0_uplinks": {"leaf1": [{"port": "100GE1/0/1", "address": "10.255.10.1/30", "neighbour": "10.255.10.2", "asn": 65004}]}},
     "already has an address"),
    ({"0_uplinks": {"leaf1": [{"port": "10GE1/0/1", "address": "10.255.10.1/30", "neighbour": "10.255.10.2", "asn": 65004}]}},
     "free physical port"),
    ({"0_uplinks": {"leaf1": [{"port": "100GE1/0/3", "address": "10.255.10.1/30", "neighbour": "10.255.10.9", "asn": 65004}]}},
     "not the other end"),
    ({"0_uplinks": {"leaf1": [{"port": "100GE1/0/3", "address": "10.255.10.1/24", "neighbour": "10.255.10.2", "asn": 65004}]}},
     "/30 or /31"),
    ({"0_uplinks": {"spine1": [{"port": "100GE1/0/3", "address": "10.255.10.1/30", "neighbour": "10.255.10.2", "asn": 65004}]}},
     "only the border pair"),
])
def test_a_side_that_cannot_be_a_border_is_refused(change, reason):
    with pytest.raises(SegmentError, match=reason):
        run(dci.set_link(linked(), "other", sides(**change)))


def test_linked_fabrics_share_the_gateway_mac():
    from orchestrator.fabric_ntp import set_gateway_mac

    nb = linked()
    run(set_gateway_mac(nb, "example-pip", "0000-5e00-0102"))
    with pytest.raises(SegmentError, match="different gateway MACs"):
        run(dci.set_link(nb, "example-pip", DCI_SIDES))


def test_a_stretched_segment_has_its_own_name_on_both_sides_and_a_picked_dci_vni():
    nb = linked()
    # example's db (VNI 10600) exists already (example_segments)
    run(Segments(nb).create("example-pip", "db2", 10600, 600))
    with pytest.raises(SegmentError, match="same name on both sides"):
        run(dci.set_segment(nb, "example-pip", "db", {"example": 10600, "example-pip": 10600}))
    # The DCI VNI is picked: the lowest free from 60000, kept when stretched again,
    # skipping either fabric's segment VNIs (example-pip's 60001 here) and other stretches (web's 60000).
    run(Segments(nb).create("example-pip", "app", 60001, 601))
    run(Segments(nb).create("example", "app", 10700, 700))
    assert run(dci.set_segment(nb, "example-pip", "web", {"example": 10500, "example-pip": 10500}))["dci_vni"] == 60000
    assert run(dci.set_segment(nb, "example-pip", "app", {"example": 10700, "example-pip": 60001}))["dci_vni"] == 60002
    with pytest.raises(SegmentError, match="stretched over DC link example-pip"):
        run(Segments(nb).delete("example", 10500))
    with pytest.raises(SegmentError, match="stretched over DC link example-pip"):
        run(Segments(nb).rename("example", 10500, "front"))


def test_what_a_link_no_longer_has_is_retired_until_the_switch_is_verified():
    nb = linked()
    narrower = sides(**{"0_uplinks": {"leaf1": DCI_SIDES[0]["uplinks"]["leaf1"]}})
    run(dci.set_link(nb, "example-pip", narrower))
    assert run(dci.get(nb))["retired"] == {"leaf2": {"peers": ["10.255.10.6"], "ports": ["100GE1/0/3"]}}
    run(dci.verified(nb, "leaf2"))
    assert run(dci.get(nb))["retired"] == {}
    run(dci.delete_link(nb, "example-pip"))
    assert set(run(dci.get(nb))["retired"]) == {"leaf1", "leaf2", "p-leaf1", "p-leaf2"}


def test_a_segment_is_stretched_only_once_the_active_borders_have_the_link():
    """VRP refuses a bridge domain's split group before a BGP peer makes it."""
    nb = linked()   # leaf1 (active) was verified with the link; a new link must be deployed again
    run(dci.delete_segment(nb, "example-pip", "web"))
    run(dci.delete_link(nb, "example-pip"))
    run(dci.set_link(nb, "example-pip", DCI_SIDES))
    assert run(dci.get(nb))["ready"] == []
    run(Segments(nb).create("example-pip", "db", 10600, 600))
    with pytest.raises(SegmentError, match="deploy DC link example-pip first: leaf1"):
        run(dci.set_segment(nb, "example-pip", "db", {"example": 10600, "example-pip": 10600}))
    run(dci.verified(nb, "leaf1"))
    run(dci.set_segment(nb, "example-pip", "db", {"example": 10600, "example-pip": 10600}))
    assert run(dci.get(nb))["ready"] == ["leaf1"]


def test_a_stretched_routed_segment_has_one_gateway_subnet_in_both_dcs():
    """The other side, routed later, takes the same subnet (outside its own pool)."""
    from orchestrator.routing import Routing

    nb = linked()
    run(Routing(nb).set_routing("example", 10500, True, firewall=None))
    run(Routing(nb).set_routing("example-pip", 10500, True, firewall=None))
    example, dc2 = (run(dci._segments(nb, f))[10500]["routing"]["gateway"] for f in ("example", "example-pip"))
    assert example == dc2 == "10.100.0.1/24"

    run(Routing(nb).set_routing("example-pip", 10500, False))
    run(Routing(nb).set_routing("example-pip", 10500, True, firewall=None, subnet="10.140.0.0/24"))   # rollback-style exact
    with pytest.raises(SegmentError, match="one gateway subnet"):
        run(dci.set_segment(nb, "example-pip", "web", {"example": 10500, "example-pip": 10500}))


def test_a_rollback_brings_links_back_and_their_segments_only_where_the_borders_are_unchanged():
    nb = linked()
    then = run(dci.get(nb))["links"]
    run(dci.delete_segment(nb, "example-pip", "web"))
    run(dci.restore(nb, "example", then))
    doc = run(dci.get(nb))
    assert doc["links"][0]["segments"] == then[0]["segments"] and doc["ready"] == ["leaf1"]
    run(dci.delete_link(nb, "example-pip"))
    run(dci.restore(nb, "example", then))
    doc = run(dci.get(nb))
    assert doc["links"][0]["segments"] == [] and doc["ready"] == [] and doc["retired"] == {}
