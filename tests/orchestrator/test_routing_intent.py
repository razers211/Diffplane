"""General routing intent: schema, retired objects, storage, API."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from orchestrator.app import app, get_netbox
from orchestrator.routing_intent import (DeviceRouting, RoutingDocument, RoutingStore, context_name, load)
from tests.collector.test_collector import example_netbox


def example_routing(**overrides) -> dict:
    """The forms verified on leaf1, as intent."""
    routing = {
        "prefix_lists": {"TEST-PFX": [{"index": 10, "action": "permit", "prefix": "10.200.0.0/16", "ge": 24, "le": 24}]},
        "as_path_filters": {"TEST-ASP": [{"index": 10, "action": "permit", "regex": "^65100$"}]},
        "community_filters": {"TEST-COMM": [{"index": 10, "action": "permit", "community": "65001:100"}]},
        "route_policies": {"TEST-IN": [
            {"node": 10, "action": "permit", "match_ip_prefix": "TEST-PFX", "apply_local_preference": 200},
            {"node": 20, "action": "deny"},
        ]},
        "static_routes": [{"prefix": "10.201.0.0/24", "next_hop": "NULL0"},
                          {"vpn": "TEST-VRF", "prefix": "10.202.0.0/24", "next_hop": "NULL0"}],
        "bgp_peers": [{"address": "192.0.2.1", "asn": 65100, "import_policy": "TEST-IN", "export_prefix_list": "TEST-PFX"},
                      {"vpn": "TEST-VRF", "address": "192.0.2.5", "asn": 65200, "import_policy": "TEST-IN"}],
        "aggregates": [{"prefix": "10.200.0.0/16", "detail_suppressed": True},
                       {"vpn": "TEST-VRF", "prefix": "10.202.0.0/16", "suppress_policy": "TEST-IN"}],
        "ospf": [{"process": 2, "vpn": "TEST-VRF", "router_id": "192.0.2.10", "areas": ["0.0.0.0"]}],
    }
    routing.update(overrides)
    return routing


def example_netbox_with_vrf():
    """The example, with VPN instance TEST-VRF on both leaves of the pair (a service on its server
    trunk): routing objects may only use VPN instances the platform creates there."""
    from orchestrator.routing import Routing

    nb = example_netbox()
    asyncio.run(Routing(nb).create_service("example", "TEST-VRF", "leaf1", "Eth-Trunk1", 3013, "10.100.113.0/29",
                                           "10.100.113.6"))
    return nb


def test_the_lab_verified_forms_are_valid():
    routing = DeviceRouting.model_validate(example_routing())
    routing.check_references()
    assert routing.bgp_peers[1].key == ("peer", "TEST-VRF", "192.0.2.5")


@pytest.mark.parametrize("change, message", [
    ({"static_routes": [{"vpn": "_management_vpn_", "prefix": "0.0.0.0/0", "next_hop": "172.24.0.1"}]}, "management"),
    ({"ospf": [{"process": 1, "vpn": "TEST-VRF", "router_id": "192.0.2.10"}]}, "greater than or equal to 2"),
    ({"aggregates": [{"prefix": "10.0.0.0/8", "detail_suppressed": True, "suppress_policy": "TEST-IN"}]}, "either"),
    ({"prefix_lists": {"P": [{"index": 10, "action": "permit", "prefix": "10.0.0.0/16", "ge": 8, "le": 24}]}},
     "shorter"),
    ({"prefix_lists": {"P": [{"index": 10, "action": "permit", "prefix": "10.0.0.0/16", "ge": 20}]}}, "not verified"),
    ({"static_routes": [{"prefix": "10.1.0.0/24", "next_hop": "NULL0"}] * 2}, "twice"),
    ({"route_policies": {"bad name": []}}, "a name is"),
    ({"bgp_peers": [{"address": "192.0.2.1", "asn": 65100, "set_med": 5}]}, "Extra inputs"),   # unverified form
])
def test_invalid_intent_is_refused(change, message):
    with pytest.raises(ValidationError, match=message):
        DeviceRouting.model_validate(example_routing(**change))


def test_references_must_exist_on_the_same_device():
    routing = DeviceRouting.model_validate(example_routing(route_policies={}))
    with pytest.raises(ValueError, match="no route-policy TEST-IN"):
        routing.check_references()


def test_removed_objects_are_retired_and_come_back_when_readded():
    full = DeviceRouting.model_validate(example_routing())
    smaller = DeviceRouting.model_validate(example_routing(
        static_routes=[{"prefix": "10.201.0.0/24", "next_hop": "NULL0"}],
        community_filters={},
    ))
    document = RoutingDocument().with_device("leaf1", full).with_device("leaf1", smaller)

    retired = document.retired["leaf1"]
    assert [s.key for s in retired.static_routes] == [("static", "TEST-VRF", "10.202.0.0/24", "NULL0")]
    assert list(retired.community_filters) == ["TEST-COMM"]

    again = document.with_device("leaf1", full)
    assert "leaf1" not in again.retired   # back in intent: managed again, nothing retired


def test_a_retired_peer_may_name_a_policy_that_is_gone():
    """Retired objects only drive removal: their references are not checked."""
    full = DeviceRouting.model_validate(example_routing())
    document = RoutingDocument().with_device("leaf1", full).with_device("leaf1", DeviceRouting())

    reloaded = load(document.data())
    assert reloaded.devices == {} and reloaded.retired["leaf1"].bgp_peers[0].import_policy == "TEST-IN"
    assert "leaf1" not in reloaded.without_retired("leaf1").retired


def test_store_writes_one_config_context_per_fabric():
    nb = example_netbox_with_vrf()
    store = RoutingStore(nb)

    asyncio.run(store.set_device("example", "leaf1+leaf2", DeviceRouting.model_validate(example_routing())))
    context = asyncio.run(nb.find_one("extras/config-contexts", name=context_name("example")))

    assert context["data"]["routing"]["devices"]["leaf1+leaf2"]["bgp_peers"][0]["asn"] == 65100
    assert asyncio.run(store.load("example")).device("leaf1+leaf2").ospf[0].process == 2

    asyncio.run(store.set_device("example", "leaf1+leaf2", DeviceRouting()))
    asyncio.run(store.clear_retired("leaf1"))   # leaf2 is planned: leaf1 alone clears
    assert asyncio.run(store.load("example")) == RoutingDocument()


def test_store_refuses_a_device_of_another_fabric():
    from orchestrator.segments import SegmentError

    with pytest.raises(SegmentError, match="not a device of fabric"):
        asyncio.run(RoutingStore(example_netbox()).set_device("example-pip", "leaf1", DeviceRouting()))


def test_api_sets_and_reads_device_routing(monkeypatch):
    from orchestrator import app as app_module

    async def unfrozen():
        return None

    nb = example_netbox_with_vrf()
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[app_module._intent_unfrozen] = unfrozen
    app.dependency_overrides[app_module.get_accounts] = lambda: type("A", (), {"sessions": None})()
    monkeypatch.setattr("orchestrator.deps.audit", lambda *_args, **_kwargs: asyncio.sleep(0))
    try:
        client = TestClient(app)
        response = client.put("/routing/example/devices/leaf1+leaf2", json=example_routing())
        assert response.status_code == 200, response.text
        assert client.get("/routing", params={"site": "example"}).json()["devices"]["leaf1+leaf2"]["ospf"][0]["vpn"] == "TEST-VRF"
        bad = client.put("/routing/example/devices/leaf1+leaf2", json=example_routing(route_policies={}))
        assert bad.status_code == 400 and "no route-policy" in bad.json()["detail"]
        assert client.put("/routing/example/devices/leaf1+leaf2", json={"bgp_peers": [{"address": "x"}]}).status_code == 422
    finally:
        for dep in (get_netbox, app_module._intent_unfrozen, app_module.get_accounts):
            app.dependency_overrides.pop(dep, None)


# -- M-LAG pairs (a pair's routing is identical on both leaves) ----------

def test_a_paired_switch_has_its_own_routing_next_to_the_pairs():
    from orchestrator.segments import SegmentError

    store = RoutingStore(example_netbox())
    for bad in ("leaf2+leaf1", "leaf1+spine1", "spine1+leaf1+leaf2"):
        with pytest.raises(SegmentError):
            asyncio.run(store.set_device("example", bad, DeviceRouting()))
    asyncio.run(store.set_device("example", "spine1", DeviceRouting()))   # an unpaired device: its own

    pfx = {"OWN": [{"index": 10, "action": "permit", "prefix": "198.51.100.0/24"}]}
    pair = {"prefix_lists": pfx, "bgp_peers": [{"address": "192.0.2.1", "asn": 65100}]}
    asyncio.run(store.set_device("example", "leaf1+leaf2", DeviceRouting.model_validate(pair)))
    # the same list, identical, may sit in both; a peer of its own on leaf2
    own = {"prefix_lists": pfx, "bgp_peers": [{"address": "192.0.2.9", "asn": 65109, "export_prefix_list": "OWN"}]}
    asyncio.run(store.set_device("example", "leaf2", DeviceRouting.model_validate(own)))
    with pytest.raises(SegmentError, match="prefix lists OWN also in the routing of leaf1\\+leaf2"):
        asyncio.run(store.set_device("example", "leaf2", DeviceRouting.model_validate(
            {"prefix_lists": {"OWN": [{"index": 10, "action": "permit", "prefix": "203.0.113.0/24"}]}})))
    with pytest.raises(SegmentError, match="peer 192.0.2.9 also in the routing of leaf2"):
        asyncio.run(store.set_device("example", "leaf1+leaf2", DeviceRouting.model_validate(
            {**pair, "bgp_peers": [*pair["bgp_peers"], {"address": "192.0.2.9", "asn": 65999}]})))
    document = asyncio.run(store.load("example"))
    assert [str(p.address) for p in document.device("leaf2").bgp_peers] == ["192.0.2.9"]
    assert [str(p.address) for p in document.device("leaf1+leaf2").bgp_peers] == ["192.0.2.1"]


def test_a_switchs_own_retired_objects_go_with_its_own_verified_deploy():
    nb = example_netbox()
    leaf2 = asyncio.run(nb.find_one("dcim/devices", name="leaf2"))
    asyncio.run(nb.update("dcim/devices", leaf2["id"], {"status": "active"}))
    store = RoutingStore(nb)
    asyncio.run(store.set_device("example", "leaf2", DeviceRouting.model_validate(
        {"bgp_peers": [{"address": "192.0.2.9", "asn": 65109}]})))
    asyncio.run(store.set_device("example", "leaf2", DeviceRouting()))
    asyncio.run(store.clear_retired("leaf1"))
    assert "leaf2" in asyncio.run(store.load("example")).retired   # not the switch it is on
    asyncio.run(store.clear_retired("leaf2"))
    assert asyncio.run(store.load("example")).retired == {}


def test_a_pairs_retired_objects_go_once_both_active_leaves_are_verified():
    nb = example_netbox_with_vrf()
    leaf2 = asyncio.run(nb.find_one("dcim/devices", name="leaf2"))
    asyncio.run(nb.update("dcim/devices", leaf2["id"], {"status": "active"}))   # both leaves built
    store = RoutingStore(nb)
    asyncio.run(store.set_device("example", "leaf1+leaf2", DeviceRouting.model_validate(example_routing())))
    asyncio.run(store.set_device("example", "leaf1+leaf2", DeviceRouting()))

    asyncio.run(store.clear_retired("leaf1"))
    assert "leaf1+leaf2" in asyncio.run(store.load("example")).retired      # leaf2 not verified yet
    asyncio.run(store.clear_retired("leaf2"))
    assert asyncio.run(store.load("example")).retired == {}


# -- addressing of a service on an M-LAG pair (the dual-active gateway is for services only) ---

def gateway(**overrides):
    from orchestrator.routing_intent import PairGateway

    return PairGateway.model_validate({"vlan": 3005, "trunk": "Eth-Trunk1", "subnet": "10.100.105.0/29", **overrides})


def test_a_subnet_becomes_addresses_for_the_sorted_leaves():
    explicit = gateway().with_addresses(["leaf2", "leaf1"])

    assert explicit.subnet is None
    assert {n: str(a) for n, a in explicit.addresses.items()} == {"leaf1": "10.100.105.1/29",
                                                                 "leaf2": "10.100.105.2/29"}
    assert str(explicit.mlag_address) == "10.100.105.3/29"


@pytest.mark.parametrize("overrides, message", [
    ({"subnet": "10.100.105.0/30"}, "/29 or larger"),
    ({"subnet": None, "addresses": {"leaf1": "10.1.0.1/29", "leaf2": "10.1.0.2/29"},
      "mlag_address": "10.2.0.3/29"}, "one subnet"),
    ({"subnet": None, "addresses": {"leaf1": "10.1.0.1/29", "leaf2": "10.1.0.1/29"},
      "mlag_address": "10.1.0.3/29"}, "distinct"),
    ({"trunk": "10GE1/0/1"}, "M-LAG Eth-Trunk"),
])
def test_invalid_pair_addressing_is_refused(overrides, message):
    with pytest.raises(ValidationError, match=message):
        gateway(**overrides)


def test_routing_intent_has_no_vlanifs_of_its_own():
    """the M-LAG dual-active gateway is for services only, not a routing object."""
    with pytest.raises(ValidationError, match="Extra inputs"):
        DeviceRouting.model_validate({"vlanifs": [{"vlan": 3005, "trunk": "Eth-Trunk1", "subnet": "10.100.105.0/29"}]})


# -- VPN instances: only where the platform creates them, and never removed under intent

def test_intent_may_only_use_vpn_instances_on_its_leaves():
    from orchestrator.segments import SegmentError

    with pytest.raises(SegmentError, match="VPN instance TEST-VRF is not on leaf1"):
        asyncio.run(RoutingStore(example_netbox()).set_device("example", "leaf1+leaf2",
                                                           DeviceRouting.model_validate(example_routing())))
    nb = example_netbox_with_vrf()
    assert asyncio.run(RoutingStore(nb).vpns("example", "leaf1+leaf2")) == ["TEST-VRF"]
    assert asyncio.run(RoutingStore(nb).vpns("example", "spine1")) == []


def intent_in(vpn: str) -> DeviceRouting:
    return DeviceRouting.model_validate({"static_routes": [{"vpn": vpn, "prefix": "10.9.0.0/16", "next_hop": "NULL0"}]})


def test_a_service_used_by_intent_cannot_be_deleted():
    from orchestrator.routing import Routing
    from orchestrator.segments import SegmentError

    nb = example_netbox_with_vrf()
    store = RoutingStore(nb)
    asyncio.run(store.set_device("example", "leaf1+leaf2", intent_in("TEST-VRF")))

    with pytest.raises(SegmentError, match="deleting service TEST-VRF would remove VPN instance TEST-VRF from leaf1"):
        asyncio.run(Routing(nb).delete_service("example", "TEST-VRF"))

    asyncio.run(store.set_device("example", "leaf1+leaf2", DeviceRouting()))
    asyncio.run(Routing(nb).delete_service("example", "TEST-VRF"))   # no longer used


def routed_web(nb, port="10GE1/0/3"):
    from orchestrator.routing import Routing
    from orchestrator.segments import Segments

    segments = Segments(nb)
    asyncio.run(segments.create("example", "web", 10500, 500))
    asyncio.run(segments.attach("example", "leaf1", port, 10500, None))
    asyncio.run(Routing(nb).set_routing("example", 10500, True, firewall=None))
    return segments


def test_a_segment_used_by_intent_stays_routed_and_attached():
    from orchestrator.routing import Routing
    from orchestrator.segments import SegmentError

    nb = example_netbox()
    segments = routed_web(nb)
    store = RoutingStore(nb)
    asyncio.run(store.set_device("example", "leaf1+leaf2", intent_in("web-500")))

    with pytest.raises(SegmentError, match="not routed would remove VPN instance web-500 from leaf1"):
        asyncio.run(Routing(nb).set_routing("example", 10500, False))
    with pytest.raises(SegmentError, match="detaching segment web from leaf1 10GE1/0/3 would remove"):
        asyncio.run(segments.detach("example", "leaf1", "10GE1/0/3", 10500))

    asyncio.run(segments.attach("example", "leaf1", "10GE1/0/5", 10500, None))
    asyncio.run(segments.detach("example", "leaf1", "10GE1/0/3", 10500))   # still attached on 10GE1/0/5


def test_a_firewall_change_that_takes_a_segment_off_a_leaf_is_refused():
    from orchestrator.routing import Routing
    from orchestrator.segments import SegmentError, Segments

    nb = example_netbox()
    routing = Routing(nb)
    asyncio.run(Segments(nb).create("example", "web", 10500, 500))
    asyncio.run(routing.create_service("example", "FW", "leaf1", "10GE1/0/10", 3001,
                                       "10.255.0.1/30", "10.255.0.2"))
    asyncio.run(routing.set_routing("example", 10500, True))   # web-500 on leaf1 only through FW
    asyncio.run(RoutingStore(nb).set_device("example", "leaf1+leaf2", intent_in("web-500")))

    with pytest.raises(SegmentError, match="changing the firewall of segment web would remove VPN instance web-500"):
        asyncio.run(routing.set_routing("example", 10500, True, firewall=None))


def test_api_lists_targets_with_their_vpn_instances(monkeypatch):
    nb = example_netbox_with_vrf()
    app.dependency_overrides[get_netbox] = lambda: nb
    try:
        targets = {t["target"]: t for t in TestClient(app).get("/routing/example/targets").json()}
        assert set(targets) == {"leaf1+leaf2", "leaf1", "leaf2", "spine1"}
        assert targets["leaf2"]["pair"] == "leaf1+leaf2" and targets["spine1"]["pair"] is None
        pair = targets["leaf1+leaf2"]
        assert [d["name"] for d in pair["devices"]] == ["leaf1", "leaf2"]
        assert [d["status"] for d in pair["devices"]] == ["active", "planned"]
        assert pair["vpns"] == ["TEST-VRF"] and targets["spine1"]["vpns"] == []
    finally:
        app.dependency_overrides.pop(get_netbox, None)


def test_renaming_a_routed_segment_renames_its_vpn_instance_in_intent():
    """the VPN instance is <segment name>-<bd>; intent follows a rename."""
    from orchestrator.segments import SegmentError

    nb = example_netbox()
    segments = routed_web(nb)
    store = RoutingStore(nb)
    asyncio.run(store.set_device("example", "leaf1+leaf2", intent_in("web-500")))

    result = asyncio.run(segments.rename("example", 10500, "frontend"))
    assert result["routed"] and result["replaces"] == "web-500" and result["vpn_instance"] == "frontend-500"
    assert result["intent_updated"] == ["leaf1+leaf2"]
    routing = asyncio.run(store.load("example")).device("leaf1+leaf2")
    assert [r.vpn for r in routing.static_routes] == ["frontend-500"]
    assert asyncio.run(store.vpns("example", "leaf1+leaf2")) == ["frontend-500"]

    for bad, message in (("web tier", "only letters"), ("9web", "starts with a letter"),
                         ("a" * 28, "too long: its VPN instance")):
        with pytest.raises(SegmentError, match=message):
            asyncio.run(segments.rename("example", 10500, bad))


# -- the fabric library  ------------------------------------------------------

OWN_LIST = {"OWN": [{"index": 10, "action": "permit", "prefix": "198.51.100.0/24"}]}
OUT_POLICY = {"OUT": [{"node": 10, "action": "permit", "match_ip_prefix": "OWN"}]}


def test_a_library_list_is_used_on_any_switch_and_written_only_where_used():
    from orchestrator.routing_intent import LibraryChange, used_by
    from orchestrator.segments import SegmentError

    store = RoutingStore(example_netbox())
    shared = LibraryChange.model_validate({"prefix_lists": OWN_LIST, "route_policies": OUT_POLICY})
    pair = DeviceRouting.model_validate({"bgp_peers": [{"address": "192.0.2.1", "asn": 65100, "export_policy": "OUT"}]})
    asyncio.run(store.set_device("example", "leaf1+leaf2", pair, shared))
    spine = DeviceRouting.model_validate({"bgp_peers": [{"address": "192.0.2.5", "asn": 65105, "export_prefix_list": "OWN"}]})
    document = asyncio.run(store.set_device("example", "spine1", spine))
    assert set(document.library.prefix_lists) == {"OWN"} and document.device("spine1").prefix_lists == {}
    assert set(document.effective("leaf1+leaf2").route_policies) == {"OUT"}
    assert set(document.effective("spine1").prefix_lists) == {"OWN"}
    assert document.effective("spine1").route_policies == {}   # OUT is not used there
    assert used_by(document) == {"prefix_lists": {"OWN": ["leaf1+leaf2", "spine1"]},
                                 "route_policies": {"OUT": ["leaf1+leaf2"]}}
    # One definition: edited once, every switch using it gets it.
    wider = [{"index": 10, "action": "permit", "prefix": "198.51.100.0/23"}]
    document = asyncio.run(store.set_device("example", "spine1", spine, LibraryChange(prefix_lists={"OWN": wider})))
    assert str(document.effective("leaf1+leaf2").prefix_lists["OWN"][0].prefix) == "198.51.100.0/23"
    # A name is fabric-wide or a target's own, never both; a used one cannot go.
    with pytest.raises(SegmentError, match="prefix list OWN is fabric-wide"):
        asyncio.run(store.set_device("example", "spine1", DeviceRouting.model_validate(
            {**spine.model_dump(mode="json", exclude_defaults=True),
             "prefix_lists": {"OWN": [{"index": 10, "action": "permit", "prefix": "203.0.113.0/24"}]}})))
    with pytest.raises(SegmentError, match="prefix list OWN"):
        asyncio.run(store.set_device("example", "spine1", spine, LibraryChange(prefix_lists={"OWN": None})))
    # An identical copy (a rollback gives a target what it ran) is the library's.
    ran = document.effective("spine1")
    assert asyncio.run(store.set_device("example", "spine1", ran)).device("spine1").prefix_lists == {}


def test_a_switch_that_stops_using_a_library_object_retires_it_there_only():
    from orchestrator.routing_intent import LibraryChange

    store = RoutingStore(example_netbox())
    peer = {"address": "192.0.2.5", "asn": 65105, "export_prefix_list": "OWN"}
    asyncio.run(store.set_device("example", "spine1", DeviceRouting.model_validate({"bgp_peers": [peer]}),
                                 LibraryChange.model_validate({"prefix_lists": OWN_LIST})))
    asyncio.run(store.set_device("example", "leaf1+leaf2", DeviceRouting.model_validate(
        {"bgp_peers": [{"address": "192.0.2.1", "asn": 65100, "import_prefix_list": "OWN"}]})))
    document = asyncio.run(store.set_device("example", "spine1", DeviceRouting.model_validate(
        {"bgp_peers": [{**peer, "export_prefix_list": None}]})))
    assert set(document.retired["spine1"].prefix_lists) == {"OWN"}
    assert "leaf1+leaf2" not in document.retired
    assert set(document.library.prefix_lists) == {"OWN"}


def test_identical_used_lists_and_policies_move_into_the_library():
    from orchestrator.routing_intent import promoted

    def target(prefix="198.51.100.0/24", used=True, extra=None):
        return {"prefix_lists": {"OWN": [{"index": 10, "action": "permit", "prefix": prefix}], **(extra or {})},
                "route_policies": {**OUT_POLICY, "ISP-OUT": [{"node": 10, "action": "deny"}]},
                "bgp_peers": [{"address": "192.0.2.1", "asn": 65100, "export_policy": "OUT" if used else None,
                               "import_policy": "ISP-OUT"}]}

    document = load({"routing": {"devices": {"a": target(), "b": target(),
                                             "c": target(extra={"X": [{"index": 5, "action": "deny", "prefix": "10.0.0.0/8"}]})}}})
    after, moved, kept = promoted(document)
    assert moved == ["prefix list OWN", "route-policy OUT"]
    assert kept == ["prefix list X: not used everywhere it is (it would leave those switches)"]
    assert set(after.device("a").route_policies) == {"ISP-OUT"}   # the simple view's stays per switch
    assert set(after.device("c").prefix_lists) == {"X"}
    assert all(after.effective(t) == document.effective(t) for t in "abc") and after.retired == {}
    differ = load({"routing": {"devices": {"a": target(), "b": target(prefix="203.0.113.0/24")}}})
    after, moved, kept = promoted(differ)
    assert moved == [] and kept[0] == "prefix list OWN: differs between a, b"


def test_api_previews_then_moves_shared_objects_into_the_library(monkeypatch):
    from orchestrator import app as app_module

    async def unfrozen():
        return None

    nb = example_netbox()
    store = RoutingStore(nb)
    for target in ("leaf1+leaf2", "spine1"):
        asyncio.run(store.set_device("example", target, DeviceRouting.model_validate(
            {"prefix_lists": OWN_LIST, "bgp_peers": [{"address": "192.0.2.1", "asn": 65100, "export_prefix_list": "OWN"}]})))
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[app_module._intent_unfrozen] = unfrozen
    app.dependency_overrides[app_module.get_accounts] = lambda: type("A", (), {"sessions": None})()
    monkeypatch.setattr("orchestrator.deps.audit", lambda *_args, **_kwargs: asyncio.sleep(0))
    try:
        client = TestClient(app)
        preview = client.post("/routing/example/library/promote")
        assert preview.json() == {"moved": ["prefix list OWN"], "kept": [], "applied": False}, preview.text
        assert asyncio.run(store.load("example")).library.prefix_lists == {}
        assert client.post("/routing/example/library/promote?apply=true").json()["applied"] is True
        assert client.get("/routing/example/library").json()["used_by"]["prefix_lists"] == {"OWN": ["leaf1+leaf2", "spine1"]}
        saved = client.put("/routing/example/targets/spine1", json={"routing": {}, "library": {}})
        assert saved.status_code == 200 and saved.json()["retired"]["spine1"]["prefix_lists"]["OWN"]
    finally:
        for dep in (get_netbox, app_module._intent_unfrozen, app_module.get_accounts):
            app.dependency_overrides.pop(dep, None)
