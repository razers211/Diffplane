"""Add switches to an existing fabric from the GUI."""

import asyncio
import ipaddress
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.app import _intent_unfrozen, app, get_accounts, get_netbox
from orchestrator.fabric_build import FabricBuilder
from orchestrator.segments import SegmentError
from seed.model import load, parse
from tests.helpers import seed
from tests.orchestrator.test_segments import FakeAccounts
from tests.seed.fake_netbox import FakeNetBox

ROOT = Path(__file__).resolve().parents[2]
MODEL = "CE6881H-48S6CQ"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def nb() -> FakeNetBox:
    netbox = FakeNetBox()
    run(seed(netbox, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    return netbox


def device(nb, name):
    return next((d for d in nb.objects["dcim/devices"].values() if d["name"] == name), None)


def iface(nb, dev, name):
    d = device(nb, dev)
    return next(i for i in nb.objects["dcim/interfaces"].values() if i["device"] == d["id"] and i["name"] == name)


def ips(nb, dev, name):
    i = iface(nb, dev, name)
    return [ip for ip in nb.objects["ipam/ip-addresses"].values() if ip.get("assigned_object_id") == i["id"]]


def add_pair(nb, **overrides):
    args = dict(leaves=["leaf3", "leaf4"], model=MODEL, role="leaf", peer_link_trunk=0,
                peer_link_members=["100GE1/0/5", "100GE1/0/6"],
                uplinks=[{"leaf": "leaf3", "leaf_port": "100GE1/0/1", "spine": "spine1", "spine_port": "100GE1/0/3"},
                         {"leaf": "leaf4", "leaf_port": "100GE1/0/1", "spine": "spine1", "spine_port": "100GE1/0/4"}])
    args.update(overrides)
    return run(FabricBuilder(nb, "example").add_leaf_pair(**args))


def test_add_a_leaf_pair_like_the_seed_would(nb):
    changes = add_pair(nb)
    assert changes
    leaf3, leaf4 = device(nb, "leaf3"), device(nb, "leaf4")
    assert leaf3["status"] == leaf4["status"] == "planned"
    assert leaf3["custom_fields"]["mlag_peer"] == leaf4["id"]
    assert len([i for i in nb.objects["dcim/interfaces"].values() if i["device"] == leaf3["id"]]) > 50   # every port
    # Peer-link, cabled port to port, tagged.
    trunk = iface(nb, "leaf3", "Eth-Trunk0")
    assert iface(nb, "leaf3", "100GE1/0/5")["lag"] == trunk["id"] and trunk["tags"]
    assert iface(nb, "leaf3", "100GE1/0/5")["cable"] == iface(nb, "leaf4", "100GE1/0/5")["cable"]
    # Uplink: one /30 from the point-to-point pool, both ends addressed.
    (spine_ip,) = ips(nb, "spine1", "100GE1/0/3")
    (leaf_ip,) = ips(nb, "leaf3", "100GE1/0/1")
    net = ipaddress.ip_interface(spine_ip["address"]).network
    assert net.prefixlen == 30 and ipaddress.ip_interface(leaf_ip["address"]) in net
    assert net.subnet_of(ipaddress.ip_network("10.11.0.0/24"))
    # Own loopbacks, one shared VTEP, the bypass /30.
    lo3, lo4 = ips(nb, "leaf3", "LoopBack0")[0], ips(nb, "leaf4", "LoopBack0")[0]
    assert lo3["address"] != lo4["address"] and lo3["address"].endswith("/32")
    assert ips(nb, "leaf3", "LoopBack1")[0]["address"] == ips(nb, "leaf4", "LoopBack1")[0]["address"]
    assert ipaddress.ip_interface(ips(nb, "leaf3", "Vlanif4093")[0]["address"]).network.prefixlen == 30
    # The new devices are in the fabric's address namespace, not the global table.
    vrf = next(v["id"] for v in nb.objects["ipam/vrfs"].values() if v["name"] == "fabric-example")
    assert lo3["vrf"] == vrf and spine_ip["vrf"] == vrf


def test_add_a_spine_with_links_to_existing_leaves(nb):
    run(FabricBuilder(nb, "example").add_spine("spine2", MODEL, [
        {"spine_port": "100GE1/0/1", "leaf": "leaf1", "leaf_port": "100GE1/0/2"},
        {"spine_port": "100GE1/0/2", "leaf": "leaf2", "leaf_port": "100GE1/0/2"},
    ]))
    spine = device(nb, "spine2")
    assert spine["status"] == "planned" and spine["role"] == next(
        r["id"] for r in nb.objects["dcim/device-roles"].values() if r["slug"] == "spine")
    assert ips(nb, "spine2", "LoopBack0") and ips(nb, "leaf1", "100GE1/0/2")
    assert iface(nb, "spine2", "100GE1/0/1")["cable"] == iface(nb, "leaf1", "100GE1/0/2")["cable"]


def test_add_refusals(nb):
    build = lambda **kw: add_pair(nb, **kw)   # noqa: E731
    up3 = {"leaf": "leaf3", "leaf_port": "100GE1/0/1", "spine": "spine1", "spine_port": "100GE1/0/3"}
    up4 = {"leaf": "leaf4", "leaf_port": "100GE1/0/1", "spine": "spine1", "spine_port": "100GE1/0/4"}
    for kwargs, message in [
        ({"leaves": ["leaf1", "leaf4"]}, "already used"),
        ({"leaves": ["leaf3", "leaf3"]}, "two different names"),
        ({"leaves": ["bad name", "leaf4"]}, "device name"),
        ({"model": "CE9999"}, "no known port layout"),
        ({"role": "spine"}, "role is"),
        ({"peer_link_members": []}, "at least one member"),
        ({"peer_link_members": ["100GE1/0/1"]}, "used twice"),             # also an uplink
        ({"uplinks": [up3]}, "leaf4 needs at least one uplink"),
        ({"uplinks": [up3, {**up4, "spine_port": "100GE1/0/1"}]}, "in use"),   # spine1's link to leaf1
        ({"uplinks": [up3, {**up4, "spine": "leaf1"}]}, "not a spine"),
        ({"uplinks": [up3, {**up4, "leaf": "leaf1"}]}, "not one of the new leaves"),
        ({"uplinks": [up3, {**up4, "leaf_port": "Eth-Trunk1"}]}, "has no port"),
        ({"uplinks": [up3, {"leaf": "leaf4"}]}, "a link needs"),
    ]:
        with pytest.raises(SegmentError, match=message):
            build(**kwargs)
    assert device(nb, "leaf3") is None   # nothing written by a refused addition
    with pytest.raises(SegmentError, match="no seeded fabric"):
        run(FabricBuilder(nb, "nope").add_spine("x", MODEL, []))
    with pytest.raises(SegmentError, match="not a leaf"):
        run(FabricBuilder(nb, "example").add_spine("spine2", MODEL, [
            {"spine_port": "100GE1/0/1", "leaf": "spine1", "leaf_port": "100GE1/0/9"}]))


def test_options_list_models_and_free_ports(nb):
    found = run(FabricBuilder(nb, "example").options())
    assert MODEL in found["models"] and found["bypass"] is True
    by_name = {d["name"]: d for d in found["devices"]}
    assert set(by_name) == {"leaf1", "leaf2", "spine1"}
    free = by_name["spine1"]["free_ports"]
    assert "100GE1/0/1" not in free and "100GE1/0/3" in free and "MEth0/0/0" not in free
    assert "100GE1/0/5" not in by_name["leaf1"]["free_ports"]   # peer-link member


def test_api_adds_a_pair_and_a_spine_audited(nb):
    accounts = FakeAccounts()
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[get_accounts] = lambda: accounts
    app.dependency_overrides[_intent_unfrozen] = lambda: None
    try:
        client = TestClient(app)
        response = client.post("/fabrics/example/leaf-pairs", json={
            "leaves": ["leaf3", "leaf4"], "model": MODEL, "peer_link_members": ["100GE1/0/5"],
            "uplinks": [{"leaf": "leaf3", "leaf_port": "100GE1/0/1", "spine": "spine1", "spine_port": "100GE1/0/3"},
                        {"leaf": "leaf4", "leaf_port": "100GE1/0/1", "spine": "spine1", "spine_port": "100GE1/0/4"}]})
        assert response.status_code == 201, response.text
        assert response.json()["devices"] == ["leaf3", "leaf4"]
        assert client.post("/fabrics/example/spines", json={"name": "leaf3", "model": MODEL}).status_code == 400
        assert client.post("/fabrics/example/spines", json={"name": "spine2", "model": MODEL}).status_code == 201
        assert client.get("/fabrics/example/build").status_code == 200
    finally:
        for dep in (get_netbox, get_accounts, _intent_unfrozen):
            app.dependency_overrides.pop(dep, None)
    assert [a for _, a, _ in accounts.events] == ["fabric.add-leaf-pair", "fabric.add-spine"]


# -- removing planned switches -------------------------------------------------------------------

def counts(nb):
    return {k: len(nb.objects.get(k, {})) for k in ("dcim/devices", "dcim/interfaces", "dcim/cables",
                                                     "ipam/ip-addresses", "ipam/prefixes")}


def test_adding_then_removing_a_pair_leaves_netbox_as_it_was(nb):
    before = counts(nb)
    add_pair(nb)
    plan = run(FabricBuilder(nb, "example").removal("leaf4"))
    assert plan["devices"] == ["leaf3", "leaf4"] and len(plan["cables"]) == 4   # 2 peer-link, 2 uplinks
    assert plan["links"] == ["spine1 100GE1/0/3", "spine1 100GE1/0/4"]
    result = run(FabricBuilder(nb, "example").remove("leaf4"))   # one leaf takes its peer with it
    assert result == {"devices": ["leaf3", "leaf4"], "links": ["spine1 100GE1/0/3", "spine1 100GE1/0/4"],
                      "unpaired": None}
    assert counts(nb) == before
    assert iface(nb, "spine1", "100GE1/0/3")["cable"] is None and not ips(nb, "spine1", "100GE1/0/3")
    add_pair(nb)   # the same names, ports and addresses can be used again
    assert device(nb, "leaf3")["status"] == "planned"


def test_removing_a_spine_frees_the_far_ends_of_its_links(nb):
    before = counts(nb)
    run(FabricBuilder(nb, "example").add_spine("spine2", MODEL, [
        {"spine_port": "100GE1/0/1", "leaf": "leaf1", "leaf_port": "100GE1/0/2"}]))
    assert run(FabricBuilder(nb, "example").remove("spine2"))["links"] == ["leaf1 100GE1/0/2"]
    assert counts(nb) == before and not ips(nb, "leaf1", "100GE1/0/2")


def test_removal_refusals(nb):
    from orchestrator.routing import Routing
    from orchestrator.trunks import Trunks

    build = FabricBuilder(nb, "example")
    with pytest.raises(SegmentError, match="leaf1 is active; decommission it first"):
        run(build.remove("leaf2"))   # its peer is active
    with pytest.raises(SegmentError, match="not a device"):
        run(build.remove("nope"))
    add_pair(nb)
    run(Trunks(nb).create("leaf3", 5, ["10GE1/0/5"], mlag=False))
    with pytest.raises(SegmentError, match="delete its trunks first"):
        run(FabricBuilder(nb, "example").remove("leaf3"))
    run(Trunks(nb).delete("leaf3", "Eth-Trunk5"))
    run(Routing(nb).create_service("example", "FW", "leaf3", "10GE1/0/9", 3000, "10.255.0.1/29", "10.255.0.6"))
    with pytest.raises(SegmentError, match="service FW"):
        run(FabricBuilder(nb, "example").remove("leaf3"))
    run(Routing(nb).delete_service("example", "FW"))
    run(FabricBuilder(nb, "example").remove("leaf3"))
    assert device(nb, "leaf3") is None


def test_api_removal_plan_and_remove(nb):
    accounts = FakeAccounts()
    add_pair(nb)
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[get_accounts] = lambda: accounts
    app.dependency_overrides[_intent_unfrozen] = lambda: None
    try:
        client = TestClient(app)
        assert client.get("/devices/leaf3/removal").json()["devices"] == ["leaf3", "leaf4"]
        assert client.get("/devices/leaf1/removal").status_code == 409
        assert client.delete("/devices/leaf1").status_code == 400
        assert client.delete("/devices/leaf3").status_code == 200
        assert client.delete("/devices/leaf3").status_code == 404
    finally:
        for dep in (get_netbox, get_accounts, _intent_unfrozen):
            app.dependency_overrides.pop(dep, None)
    assert [a for _, a, _ in accounts.events] == ["fabric.remove-device"]


# -- several switches at once, lone leaves ---------------------------------------------

def batch():
    up = lambda spine, sp, leaf, lp: {"spine": spine, "spine_port": sp, "leaf": leaf, "leaf_port": lp}   # noqa: E731
    return {
        "spines": [{"name": "spine2", "model": MODEL, "management": "172.24.0.126/24"}],
        "pairs": [{"leaves": ["leaf3", "leaf4"], "model": MODEL, "role": "leaf", "peer_link_trunk": 0,
                   "peer_link_members": ["100GE1/0/5", "100GE1/0/6"]}],
        "leaves": [{"name": "leaf5", "model": MODEL, "role": "border-leaf", "management": "172.24.0.125/24"}],
        "links": [up("spine1", "100GE1/0/3", "leaf3", "100GE1/0/1"),
                  up("spine1", "100GE1/0/4", "leaf4", "100GE1/0/1"),
                  up("spine1", "100GE1/0/6", "leaf5", "100GE1/0/1"),
                  up("spine2", "100GE1/0/1", "leaf1", "100GE1/0/2"),     # new spine to an existing leaf
                  up("spine2", "100GE1/0/2", "leaf3", "100GE1/0/2"),     # new spine to new leaves
                  up("spine2", "100GE1/0/3", "leaf5", "100GE1/0/2")],
    }


def test_a_batch_of_pairs_lone_leaves_and_spines(nb):
    result = run(FabricBuilder(nb, "example").add(batch()))
    assert result["devices"] == ["spine2", "leaf3", "leaf4", "leaf5"]
    assert result["active"] == ["leaf5", "spine2"]
    assert device(nb, "spine2")["status"] == device(nb, "leaf5")["status"] == "active"
    assert device(nb, "leaf3")["status"] == "planned"
    assert nb.objects["ipam/ip-addresses"][device(nb, "leaf5")["primary_ip4"]]["address"] == "172.24.0.125/24"
    # The lone leaf: no peer, its own VTEP, different from the pair's.
    assert not device(nb, "leaf5")["custom_fields"].get("mlag_peer")
    lone_vtep = ips(nb, "leaf5", "LoopBack1")[0]["address"]
    assert lone_vtep != ips(nb, "leaf3", "LoopBack1")[0]["address"]
    assert ips(nb, "leaf3", "LoopBack1")[0]["address"] == ips(nb, "leaf4", "LoopBack1")[0]["address"]
    assert ips(nb, "spine2", "100GE1/0/2") and ips(nb, "leaf1", "100GE1/0/2")


def test_batch_refusals(nb):
    def refused(change, message):
        data = batch()
        change(data)
        with pytest.raises(SegmentError, match=message):
            run(FabricBuilder(nb, "example").add(data))

    refused(lambda d: d["leaves"][0].update(name="leaf3"), "appears twice")
    refused(lambda d: d["links"].append({"spine": "spine1", "spine_port": "100GE1/0/9", "leaf": "leaf1",
                                         "leaf_port": "100GE1/0/9"}), "at least one new switch")
    refused(lambda d: d["spines"][0].update(management="172.24.0.125/24"), "given twice")
    refused(lambda d: d["spines"][0].update(management="172.24.0.11/24"), "already a management address")
    refused(lambda d: d["spines"][0].update(management="172.24.0.126"), "with its mask")
    refused(lambda d: d.update(links=[l for l in d["links"] if l["leaf"] != "leaf5"]), "leaf5 needs at least one uplink")
    refused(lambda d: d["leaves"][0].update(role="spine"), "role is")
    refused(lambda d: d["links"][0].update(spine="leaf4"), "not a spine")
    refused(lambda d: d.update(spines=[], pairs=[], leaves=[], links=[]), "nothing to add")
    assert device(nb, "leaf3") is None and device(nb, "spine2") is None   # nothing written


def test_a_whole_batch_comes_out_again(nb):
    before = counts(nb)
    run(FabricBuilder(nb, "example").add({**batch(), "spines": [{"name": "spine2", "model": MODEL}],
                                      "leaves": [{"name": "leaf5", "model": MODEL, "role": "leaf"}]}))
    for name in ("leaf5", "leaf3", "spine2"):   # a lone leaf alone, the pair whole
        run(FabricBuilder(nb, "example").remove(name))
    assert counts(nb) == before


def test_api_adds_a_batch(nb):
    accounts = FakeAccounts()
    app.dependency_overrides[get_netbox] = lambda: nb
    app.dependency_overrides[get_accounts] = lambda: accounts
    app.dependency_overrides[_intent_unfrozen] = lambda: None
    try:
        response = TestClient(app).post("/fabrics/example/switches", json=batch())
        assert response.status_code == 201, response.text
        assert response.json()["active"] == ["leaf5", "spine2"]
    finally:
        for dep in (get_netbox, get_accounts, _intent_unfrozen):
            app.dependency_overrides.pop(dep, None)
    assert [a for _, a, _ in accounts.events] == ["fabric.add-switches"]


# -- M-LAG spine pairs in a centralized fabric --------------------------------

def central_data() -> dict:
    import yaml

    data = yaml.safe_load((ROOT / "tests" / "fixtures" / "example-small.yaml").read_text())
    data["fabric"].update(architecture="centralized", gateway_pair=["leaf1", "leaf2"])
    data["leaf_pairs"][0]["role"] = "border-leaf"
    return data


@pytest.fixture
def central() -> FakeNetBox:
    netbox = FakeNetBox()
    run(seed(netbox, parse(central_data())))
    return netbox


def spine_pair(**overrides) -> dict:
    return {"spines": ["spine1", "spine2"], "model": MODEL, "peer_link_trunk": 0,
            "peer_link_members": ["100GE1/0/5", "100GE1/0/6"], **overrides}


SPINE2_LINKS = [{"spine": "spine2", "spine_port": "100GE1/0/1", "leaf": "leaf1", "leaf_port": "100GE1/0/2"},
                {"spine": "spine2", "spine_port": "100GE1/0/2", "leaf": "leaf2", "leaf_port": "100GE1/0/2"}]


def gateway_pair(nb) -> list[str]:
    context = next(c for c in nb.objects["extras/config-contexts"].values() if c["name"] == "fabric-example")
    return context["data"]["fabric"]["gateway_pair"]


def test_an_existing_spine_and_a_new_one_become_a_pair(central):
    before = counts(central)
    result = run(FabricBuilder(central, "example").add({"spine_pairs": [spine_pair()], "links": SPINE2_LINKS}))
    assert result["devices"] == ["spine2"] and result["gateway_pair"] is None
    spine1, spine2 = device(central, "spine1"), device(central, "spine2")
    assert spine2["status"] == "planned"
    assert spine1["custom_fields"]["mlag_peer"] == spine2["id"] and spine2["custom_fields"]["mlag_peer"] == spine1["id"]
    for name in ("spine1", "spine2"):
        assert iface(central, name, "100GE1/0/5")["lag"] == iface(central, name, "Eth-Trunk0")["id"]
        assert ips(central, name, "Vlanif4093")
    assert ips(central, "spine1", "LoopBack1")[0]["address"] == ips(central, "spine2", "LoopBack1")[0]["address"]
    assert gateway_pair(central) == ["leaf1", "leaf2"]   # an extra pair: gateways stay

    plan = run(FabricBuilder(central, "example").removal("spine2"))
    assert plan["devices"] == ["spine2"] and plan["unpair"] == "spine1"
    run(FabricBuilder(central, "example").remove("spine2"))
    assert counts(central) == before   # spine1 is a single spine again
    assert not device(central, "spine1")["custom_fields"].get("mlag_peer")


def test_a_new_spine_pair_can_take_over_the_gateways_and_reseeding_keeps_it(central):
    pair = spine_pair(spines=["spine2", "spine3"], peer_link_trunk=0, gateway=True,
                      management={"spine3": "172.24.0.127/24"})
    result = run(FabricBuilder(central, "example").add({"spine_pairs": [pair]}))
    assert result["gateway_pair"] == gateway_pair(central) == ["spine2", "spine3"]
    assert result["active"] == ["spine3"]
    run(seed(central, parse(central_data())))
    assert gateway_pair(central) == ["spine2", "spine3"]
    with pytest.raises(SegmentError, match="gateway pair"):
        run(FabricBuilder(central, "example").removal("spine2"))


def test_spine_pair_refusals(nb, central):
    def refused(netbox, data, message):
        with pytest.raises(SegmentError, match=message):
            run(FabricBuilder(netbox, "example").add(data))

    refused(nb, {"spine_pairs": [spine_pair()]}, "symmetric")
    refused(central, {"spine_pairs": [spine_pair(spines=["spine1", "leaf1"])]}, "both exist")
    refused(central, {"spine_pairs": [spine_pair(spines=["leaf1", "spine2"])]}, "not a spine")
    refused(central, {"spine_pairs": [spine_pair(peer_link_members=["100GE1/0/1"])]}, "in use")   # its link to leaf1
    refused(central, {"spine_pairs": [spine_pair(spines=["spine2", "spine3"], gateway=True),
                                      spine_pair(spines=["spine4", "spine5"], gateway=True)]}, "only one pair")
    refused(central, {"spine_pairs": [spine_pair()], "links": [{**SPINE2_LINKS[0], "spine_port": "100GE1/0/5"}]},
            "used twice")
    assert device(central, "spine2") is None   # nothing written
