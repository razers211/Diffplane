import asyncio
from pathlib import Path

import pytest

from orchestrator.routing import Routing
from orchestrator.segments import SegmentError, Segments
from tests.helpers import seed
from seed.model import load
from tests.orchestrator.test_segments import second_dc
from tests.seed.fake_netbox import FakeNetBox

ROOT = Path(__file__).resolve().parents[2]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def nb() -> FakeNetBox:
    netbox = FakeNetBox()
    asyncio.run(seed(netbox, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    asyncio.run(seed(netbox, second_dc()))
    return netbox


def context(nb, fabric="example"):
    return next(c for c in nb.objects["extras/config-contexts"].values() if c["name"] == f"segments-{fabric}")["data"]


FW = dict(device="leaf1", port="10GE1/0/10", vlan=3001, address="10.255.0.1/29", next_hop="10.255.0.2")


def with_segments(nb):
    s = Segments(nb)
    run(s.create("example", "web", 10500, 500))
    run(s.create("example", "db", 10600, 600))
    return s


def test_create_service_and_publish_it(nb):
    r = Routing(nb)

    run(r.create_service("example", "FIREWALL", **FW))

    single = {"asn": None, "kind": "firewall", "devices": ["leaf1"], "addresses": None, "ipv6": None}
    assert run(r.services("example")) == [{"name": "FIREWALL", "vrf": "FIREWALL", "rd_vlan": FW["vlan"], **FW, **single}]
    assert context(nb)["services"] == {"FIREWALL": {**FW, **single}}
    (vrf,) = [v for v in nb.objects["ipam/vrfs"].values() if v["name"] == "example:FIREWALL"]
    assert vrf["custom_fields"]["service_vlan"] == 3001


@pytest.mark.parametrize("changes, message", [
    ({"name": "SEG-1"}, "service name"), ({"name": "_management_vpn_"}, "service name"),
    ({"name": "9x"}, "service name"), ({"vlan": 1}, "VLAN must be"), ({"vlan": 4093}, "bypass VLAN"),
    ({"next_hop": "10.255.1.2"}, "another host"), ({"next_hop": "10.255.0.1"}, "another host"),
    ({"address": "10.255.0.1/31"}, "another host"), ({"port": "Eth-Trunk1"}, "Vlanif subnet"),
    ({"port": "Eth-Trunk0"}, "peer-link"), ({"addresses": {"leaf1": "10.255.0.1/29"}}, "M-LAG pair"),
    ({"port": "100GE1/0/1"}, "routed fabric link"), ({"device": "dc2-leaf1"}, "not in fabric example"),
    ({"address": "nope"}, "invalid address"),
])
def test_service_validation(nb, changes, message):
    with pytest.raises(SegmentError, match=message):
        run(Routing(nb).create_service("example", **{"name": "FIREWALL", **FW, **changes}))


def test_service_ports_are_dedicated(nb):
    s, r = with_segments(nb), Routing(nb)
    run(s.attach("example", "leaf1", "10GE1/0/11", 10500, None))
    run(r.create_service("example", "FIREWALL", **FW))

    with pytest.raises(SegmentError, match="carries segments"):
        run(r.create_service("example", "FW2", **{**FW, "port": "10GE1/0/11", "vlan": 3002}))
    with pytest.raises(SegmentError, match="firewall service port"):
        run(s.attach("example", "leaf1", "10GE1/0/10", 10500, 500))
    run(r.create_service("example", "FW2", **{**FW, "vlan": 3002, "address": "10.255.2.1/30", "next_hop": "10.255.2.2"}))
    # a port may carry several services: the service form is offered it, segments never
    assert "10GE1/0/10" not in [p["name"] for p in run(s.ports("leaf1"))]
    assert "10GE1/0/10" in [p["name"] for p in run(s.ports("leaf1", with_services=True))]


def test_routed_segment_gets_a_gateway_and_the_default_firewall(nb):
    s, r = with_segments(nb), Routing(nb)
    run(r.create_service("example", "FIREWALL", **FW))

    result = run(r.set_routing("example", 10500, True))

    assert result == {"vni": 10500, "routed": True, "firewall": "FIREWALL", "peers": []}
    assert context(nb)["segments"]["10500"]["routing"] == {
        "gateway": "10.100.0.1/24", "peers": [], "firewall": "FIREWALL"}
    gateway = next(p for p in nb.objects["ipam/prefixes"].values() if p["prefix"] == "10.100.0.0/24")
    assert gateway["vrf"] is not None and gateway["description"] == "example:web gateway"


def test_a_segment_takes_the_operators_own_network_inside_or_outside_the_pool(nb):
    s, r = with_segments(nb), Routing(nb)
    run(r.set_routing("example", 10500, True, firewall=None, subnet="192.168.50.0/26", chosen=True))   # outside the pool
    assert context(nb)["segments"]["10500"]["routing"]["gateway"] == "192.168.50.1/26"
    run(r.set_routing("example", 10600, True, firewall=None, subnet="10.100.7.0/24", chosen=True))     # inside it
    assert context(nb)["segments"]["10600"]["routing"]["gateway"] == "10.100.7.1/24"
    # the next free one skips what the operator took
    run(r.segments.create("example", "app", 10700, 700))
    run(r.set_routing("example", 10700, True, firewall=None))
    assert context(nb)["segments"]["10700"]["routing"]["gateway"] == "10.100.0.1/24"


def test_an_own_network_overlapping_the_fabric_or_malformed_is_refused(nb):
    s, r = with_segments(nb), Routing(nb)
    with pytest.raises(SegmentError, match="overlaps"):
        run(r.set_routing("example", 10500, True, firewall=None, subnet="10.10.0.0/24", chosen=True))   # the loopback pool
    for bad, why in (("10.20.30.0/31", "/8 to /30"), ("not a net", "not an IPv4"), ("fd00::/64", "IPv4")):
        with pytest.raises(SegmentError, match=why):
            run(r.set_routing("example", 10500, True, firewall=None, subnet=bad, chosen=True))
    assert context(nb)["segments"]["10500"].get("routing") is None


def test_routed_without_firewall_and_with_explicit_peers(nb):
    s, r = with_segments(nb), Routing(nb)
    run(r.set_routing("example", 10600, True, firewall=None))

    run(r.set_routing("example", 10500, True, firewall=None, peers=[10600]))

    segments = context(nb)["segments"]
    # db was routed first, so it got the pool's first /24
    assert segments["10600"]["routing"] == {"gateway": "10.100.0.1/24", "peers": [10500], "firewall": None}
    assert segments["10500"]["routing"] == {"gateway": "10.100.1.1/24", "peers": [10600], "firewall": None}


def test_peer_must_be_routed_and_exist(nb):
    s, r = with_segments(nb), Routing(nb)

    with pytest.raises(SegmentError, match="not routed"):
        run(r.set_routing("example", 10500, True, peers=[10600]))
    with pytest.raises(SegmentError, match="no segment with VNI 999"):
        run(r.set_routing("example", 10500, True, peers=[999]))
    with pytest.raises(SegmentError, match="no firewall service"):
        run(r.set_routing("example", 10500, True, firewall="NOPE"))


def test_unrouting_frees_the_gateway_and_removes_peerings(nb):
    s, r = with_segments(nb), Routing(nb)
    run(r.set_routing("example", 10600, True, firewall=None))
    run(r.set_routing("example", 10500, True, firewall=None, peers=[10600]))

    run(r.set_routing("example", 10500, False))

    segments = context(nb)["segments"]
    assert "routing" not in segments["10500"]
    assert segments["10600"]["routing"]["peers"] == []
    assert not any(p["prefix"] == "10.100.0.0/24" and (p.get("description") or "").startswith("example:web")
                   for p in nb.objects["ipam/prefixes"].values())
    run(s.delete("example", 10500))


def test_routed_segment_and_used_service_cannot_be_deleted(nb):
    s, r = with_segments(nb), Routing(nb)
    run(r.create_service("example", "FIREWALL", **FW))
    run(r.set_routing("example", 10500, True))

    with pytest.raises(SegmentError, match="is routed"):
        run(s.delete("example", 10500))
    with pytest.raises(SegmentError, match="firewall of web"):
        run(r.delete_service("example", "FIREWALL"))


def test_gateway_subnets_repeat_per_fabric(nb):
    r = Routing(nb)
    run(Segments(nb).create("example", "web", 10500, 500))
    run(Segments(nb).create("dc2", "web", 10500, 500))

    run(r.set_routing("example", 10500, True, firewall=None))
    run(r.set_routing("dc2", 10500, True, firewall=None))

    assert context(nb, "example")["segments"]["10500"]["routing"]["gateway"] == \
        context(nb, "dc2")["segments"]["10500"]["routing"]["gateway"] == "10.100.0.1/24"


def test_http_routing_flow_is_audited(nb):
    from fastapi.testclient import TestClient

    from orchestrator.app import _intent_unfrozen, app, get_accounts, get_netbox
    from tests.orchestrator.test_segments import FakeAccounts

    accounts = FakeAccounts()
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[get_accounts] = lambda: accounts
    app.dependency_overrides[_intent_unfrozen] = lambda: None   # tested in test_intent_freeze.py
    client = TestClient(app)
    try:
        assert client.post("/segments", json={"site": "example", "name": "web", "vni": 10500, "bd_id": 500}).status_code == 201
        service = {"site": "example", "name": "FW", "device": "leaf1", "port": "10GE1/0/4", "vlan": 3000,
                   "address": "10.255.0.1/30", "next_hop": "10.255.0.2"}
        assert client.post("/services", json=service).status_code == 201
        assert client.post("/services", json=service).status_code == 400
        response = client.patch("/services/example/FW", json={"asn": 65100})
        assert response.status_code == 200 and response.json()["asn"] == 65100
        assert client.patch("/services/example/FW", json={"asn": 65001}).status_code == 400   # fabric ASN
        assert [s["name"] for s in client.get("/services", params={"site": "example"}).json()] == ["FW"]

        response = client.put("/segments/example/10500/routing", json={"routed": True})
        assert response.status_code == 200 and response.json()["firewall"] == "FW"
        assert client.get("/segments", params={"site": "example"}).json()[0]["routing"]["firewall"] == "FW"
        assert client.delete("/services/example/FW").status_code == 400          # still used by web
        response = client.put("/segments/example/10500/routing", json={"routed": True, "firewall": None})
        assert response.json()["firewall"] is None                            # explicit null: no firewall
        assert client.put("/segments/example/10500/routing", json={"routed": True, "firewall": "FW"}).status_code == 200
        assert client.put("/segments/example/10500/routing", json={"routed": False}).status_code == 200
        assert client.delete("/services/example/FW").status_code == 200
        assert client.get("/services", params={"site": "nowhere"}).status_code == 404

        assert [a for _, a, _ in accounts.events] == [
            "segment.create", "service.create", "service.change", "segment.routing", "segment.routing", "segment.routing",
            "segment.routing", "service.delete"]
    finally:
        for dep in (get_netbox, get_accounts, _intent_unfrozen):
            app.dependency_overrides.pop(dep, None)


def test_firewall_asn_is_set_validated_and_published(nb):
    r = Routing(nb)
    run(r.create_service("example", "FIREWALL", **FW, asn=65100))
    assert context(nb)["services"]["FIREWALL"]["asn"] == 65100

    assert run(r.set_service_asn("example", "FIREWALL", 65200))["asn"] == 65200
    assert context(nb)["services"]["FIREWALL"]["asn"] == 65200
    run(r.set_service_asn("example", "FIREWALL", None))
    assert context(nb)["services"]["FIREWALL"]["asn"] is None

    with pytest.raises(SegmentError, match="differ from the fabric ASN"):
        run(r.set_service_asn("example", "FIREWALL", 65001))
    with pytest.raises(SegmentError, match="1-4294967295"):
        run(r.set_service_asn("example", "FIREWALL", 0))
    with pytest.raises(SegmentError, match="no service"):
        run(r.set_service_asn("example", "NOPE", 65100))


# -- ISP services ----------------------------------------------------------

ISP = dict(device="leaf1", port="10GE1/0/11", vlan=3002, address="10.255.1.1/30", next_hop="10.255.1.2")


def test_an_isp_service_is_a_container_without_asn(nb):
    r = Routing(nb)
    run(r.create_service("example", "ISP-A", **ISP, kind="isp"))

    assert context(nb)["services"]["ISP-A"]["kind"] == "isp"
    with pytest.raises(SegmentError, match="ISP service has no ASN"):
        run(r.create_service("example", "ISP-B", **{**ISP, "port": "10GE1/0/12", "vlan": 3003}, asn=65100, kind="isp"))
    with pytest.raises(SegmentError, match="ISP service has no ASN"):
        run(r.set_service_asn("example", "ISP-A", 65100))
    with pytest.raises(SegmentError, match="one of"):
        run(r.create_service("example", "X", **{**ISP, "port": "10GE1/0/12", "vlan": 3003}, kind="router"))


def test_segments_default_route_only_through_a_firewall(nb):
    r = Routing(nb)
    with_segments(nb)
    run(r.create_service("example", "ISP-A", **ISP, kind="isp"))

    assert run(r.set_routing("example", 10500, True))["firewall"] is None        # the default skips ISP services
    with pytest.raises(SegmentError, match="no firewall service 'ISP-A'"):
        run(r.set_routing("example", 10600, True, firewall="ISP-A"))


def test_deleting_an_isp_service_retires_no_routes(nb):
    from orchestrator.routing_intent import RoutingStore

    r = Routing(nb)
    run(r.create_service("example", "ISP-A", **ISP, kind="isp"))
    run(r.delete_service("example", "ISP-A"))

    assert run(RoutingStore(nb).load("example")).retired_services == {}


def test_the_isp_session_is_a_routing_intent_peer(nb):
    from orchestrator.routing_intent import DeviceRouting, RoutingStore

    run(Routing(nb).create_service("example", "ISP-A", **ISP, kind="isp"))
    run(Routing(nb).create_service("example", "FW", **FW))
    store = RoutingStore(nb)
    isp_peer = DeviceRouting.model_validate({"bgp_peers": [{"vpn": "ISP-A", "address": "10.255.1.2", "asn": 64600}]})
    run(store.set_device("example", "leaf1+leaf2", isp_peer))   # allowed: it is how an ISP session is set

    fw_peer = DeviceRouting.model_validate({"bgp_peers": [{"vpn": "FW", "address": "10.255.0.2", "asn": 64700}]})
    with pytest.raises(SegmentError, match="firewall of service FW"):
        run(store.set_device("example", "leaf1+leaf2", fw_peer))


# -- services on an M-LAG pair ---------------------------------------------

PAIR_FW = dict(device="leaf2", port="Eth-Trunk1", vlan=3005, address="10.100.105.0/29", next_hop="10.100.105.6")


def test_a_service_on_a_server_trunk_is_the_pairs(nb):
    r = Routing(nb)

    created = run(r.create_service("example", "FW", **PAIR_FW, asn=65100))
    (service,) = run(r.services("example"))

    assert created["devices"] == service["devices"] == ["leaf1", "leaf2"]
    assert service["address"] == "10.100.105.3/29"   # the shared M-LAG address: the firewall's gateway
    assert service["addresses"] == {"leaf1": "10.100.105.1/29", "leaf2": "10.100.105.2/29"}
    assert context(nb)["services"]["FW"]["devices"] == ["leaf1", "leaf2"]


def test_pair_service_addresses_may_be_typed_one_by_one(nb):
    r = Routing(nb)
    addresses = {"leaf1": "10.100.105.4/29", "leaf2": "10.100.105.5/29"}

    run(r.create_service("example", "FW", **{**PAIR_FW, "address": "10.100.105.1/29", "addresses": addresses}))

    (service,) = run(r.services("example"))
    assert service["address"] == "10.100.105.1/29" and service["addresses"] == addresses


@pytest.mark.parametrize("changes, message", [
    ({"address": "10.100.105.1/29"}, "Vlanif subnet"),                 # a host, no per-leaf addresses
    ({"address": "10.100.105.0/30"}, "/29 or larger"),
    ({"next_hop": "10.100.105.3"}, "another host"), ({"next_hop": "10.100.105.1"}, "another host"),
    ({"next_hop": "10.100.106.1"}, "another host"),
    ({"address": "10.100.105.3/29", "addresses": {"leaf1": "10.100.105.1/29", "spine1": "10.100.105.2/29"}},
     "one address for each"),
])
def test_pair_service_validation(nb, changes, message):
    with pytest.raises(SegmentError, match=message):
        run(Routing(nb).create_service("example", "FW", **{**PAIR_FW, **changes}))


def test_a_service_trunk_is_dedicated_on_both_leaves(nb):
    s, r = with_segments(nb), Routing(nb)
    run(r.create_service("example", "FW", **PAIR_FW))

    for leaf in ("leaf1", "leaf2"):
        with pytest.raises(SegmentError, match="service port"):
            run(s.attach("example", leaf, "Eth-Trunk1", 10500, 500))
        assert "Eth-Trunk1" not in [p["name"] for p in run(s.ports(leaf))]
    run(r.create_service("example", "FW2", **{**PAIR_FW, "device": "leaf1", "vlan": 3006}))   # another service there
    with pytest.raises(SegmentError, match="VLAN 3005 is already used"):
        run(r.create_service("example", "FW3", **{**FW, "vlan": 3005}))


def test_a_pair_service_needs_a_free_trunk(nb):
    s, r = with_segments(nb), Routing(nb)
    run(s.attach("example", "leaf1", "Eth-Trunk1", 10500, 500))     # written on both leaves
    with pytest.raises(SegmentError, match="carries segments"):
        run(r.create_service("example", "FW", **PAIR_FW))
    run(s.detach("example", "leaf1", "Eth-Trunk1", 10500))
    run(r.create_service("example", "FW", **PAIR_FW))                     # free again


def test_deleting_a_pair_firewall_retires_its_routes_on_its_active_leaves(nb):
    from orchestrator.routing_intent import RoutingStore

    r, store = Routing(nb), RoutingStore(nb)
    leaf2 = run(nb.find_one("dcim/devices", name="leaf2"))
    run(r.create_service("example", "FW", **PAIR_FW))
    run(r.delete_service("example", "FW"))
    # leaf2 is planned: nothing was ever deployed there (example).
    assert run(store.load("example")).retired_services == {"leaf1": ["FW 10.100.105.6"]}

    run(nb.update("dcim/devices", leaf2["id"], {"status": "active"}))
    run(r.create_service("example", "FW", **PAIR_FW))
    run(r.delete_service("example", "FW"))
    assert run(store.load("example")).retired_services == {"leaf1": ["FW 10.100.105.6"], "leaf2": ["FW 10.100.105.6"]}


def test_a_verified_leaf_drops_stale_entries_of_its_planned_peer(nb):
    from orchestrator.routing_intent import RoutingStore

    store = RoutingStore(nb)
    document = run(store.load("example")).with_retired_service("leaf2", "FW")   # as left by the old code
    run(store.save("example", document))

    run(store.clear_retired("leaf1"))

    assert run(store.load("example")).retired_services == {}
