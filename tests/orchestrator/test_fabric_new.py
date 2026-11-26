"""A new fabric from the GUI, and the switch inventory."""

import asyncio
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from orchestrator.app import _intent_unfrozen, app, get_accounts, get_netbox
from orchestrator.fabric_build import FabricBuilder
from orchestrator.fabric_new import address_plan, create
from orchestrator.inventory import Inventory
from orchestrator.segments import SegmentError
from seed.model import load
from tests.helpers import seed
from tests.orchestrator.test_segments import FakeAccounts
from tests.seed.fake_netbox import FakeNetBox

ROOT = Path(__file__).resolve().parents[2]
MODEL = "CE6881H-48S6CQ"


def run(coro):
    return asyncio.run(coro)


def wizard(path: Path, **changes) -> tuple[dict, dict]:
    """A fabric YAML as the wizard sends it: settings, and its switches as one Add switches batch
    (server trunks left out: the GUI makes those afterwards)."""
    data = yaml.safe_load(path.read_text())
    f, services = data["fabric"], data["services"]
    settings = {"name": f["name"], "asn": f["asn"], "architecture": f.get("architecture", "symmetric"),
                "vendor": f.get("vendor", "huawei"),
                "prefixes": f["prefixes"], "bypass_vlan": f["mlag"]["bypass_vlan"], "gateway_mac": f["gateway_mac"],
                "ntp_servers": services.get("ntp_servers") or [],
                "snmp_location": (services.get("snmp") or {}).get("location"),
                "snmp_contact": (services.get("snmp") or {}).get("contact"), **changes}
    gateways = sorted(f.get("gateway_pair") or [])
    in_pair = set((data.get("spine_pair") or {}).get("spines") or [])
    batch = {
        "spines": [{"name": s["name"], "model": s["model"]} for s in data["spines"] if s["name"] not in in_pair],
        "pairs": [{"leaves": p["leaves"], "model": p["model"], "role": p.get("role", "leaf"),
                   "peer_link_trunk": p["peer_link"]["trunk"], "peer_link_members": p["peer_link"]["members"],
                   "gateway": sorted(p["leaves"]) == gateways} for p in data["leaf_pairs"]],
        "spine_pairs": [{"spines": data["spine_pair"]["spines"], "model": data["spines"][0]["model"],
                         "peer_link_trunk": data["spine_pair"]["peer_link"]["trunk"],
                         "peer_link_members": data["spine_pair"]["peer_link"]["members"],
                         "gateway": True}] if data.get("spine_pair") else [],
        "links": data["links"],
    }
    return settings, batch


def devices(nb, site):
    return {d["name"]: d for d in nb.objects.get("dcim/devices", {}).values()
            if nb.objects["dcim/sites"][d["site"]]["slug"] == site}


def context(nb, site):
    return next(c for c in nb.objects["extras/config-contexts"].values() if c["name"] == f"fabric-{site}")["data"]


def test_the_proposed_address_plan_is_dc1s_split():
    assert address_plan("10.150.0.0/16") == {
        "loopback": "10.150.0.0/24", "vtep": "10.150.1.0/24", "p2p": "10.150.2.0/24", "mlag_bypass": "10.150.3.0/24",
        "pip": "10.150.4.0/24", "gateways": "10.150.128.0/17"}
    assert address_plan("172.20.0.0/20")["gateways"] == "172.20.8.0/21"
    # a /21 has four /24s below its gateways: the PIPs take the bypass /24's upper half
    assert {k: v for k, v in address_plan("10.9.0.0/21").items() if k in ("mlag_bypass", "pip")} == {
        "mlag_bypass": "10.9.3.0/25", "pip": "10.9.3.128/25"}
    for bad, why in (("8.8.0.0/16", "not private"), ("10.1.2.0/24", "/8 to /21"), ("10.0.0.300/16", "invalid")):
        with pytest.raises(SegmentError, match=why):
            address_plan(bad)


def test_a_new_fabric_is_what_the_seed_makes_of_the_same_yaml():
    nb = FakeNetBox()
    settings, batch = wizard(ROOT / "seed" / "fabrics" / "example.yaml", name="gui1")
    result = run(create(nb, settings, batch))
    assert result["site"] == "gui1"
    assert sorted(devices(nb, "gui1")) == ["leaf1", "leaf2", "leaf3", "leaf4", "spine1", "spine2"]
    assert all(d["status"] == "planned" for d in devices(nb, "gui1").values())
    fabric = context(nb, "gui1")["fabric"]
    assert fabric["asn"] == 65001 and fabric["architecture"] == "symmetric" and fabric["gateway_mac"] == "0000-5e00-0101"
    assert fabric["ntp_servers"] == ["192.0.2.1", "192.0.2.2"] and fabric["mlag"] == {"bypass_vlan": 4093}


def test_a_new_arista_fabric_has_arista_switches_and_eos_names():
    nb = FakeNetBox()
    settings, batch = wizard(ROOT / "tests" / "fixtures" / "example-arista.yaml")
    run(create(nb, settings, batch))
    assert context(nb, "eos")["fabric"]["vendor"] == "arista"
    makers = {m["id"]: m["name"] for m in nb.objects["dcim/manufacturers"].values()}
    assert {makers[t["manufacturer"]] for t in nb.objects["dcim/device-types"].values()} == {"Arista"}
    leaf = devices(nb, "eos")["eos-leaf1"]
    names = {i["name"] for i in nb.objects["dcim/interfaces"].values() if i["device"] == leaf["id"]}
    assert {"Loopback0", "Loopback1", "Port-Channel10", "Vlan4093"} <= names and not any("LoopBack" in n for n in names)


def test_a_fabric_is_one_vendor_in_new_fabric_and_add_switches():
    settings, batch = wizard(ROOT / "tests" / "fixtures" / "example-arista.yaml")
    mixed = {**batch, "spines": [{"name": "eos-spine1", "model": MODEL}]}
    with pytest.raises(SegmentError, match="not a arista switch"):
        run(create(FakeNetBox(), settings, mixed))
    nb = FakeNetBox()
    run(seed(nb, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    with pytest.raises(SegmentError, match="vEOS-lab is not a huawei switch"):
        run(FabricBuilder(nb, "example").add({"spines": [{"name": "spine9", "model": "vEOS-lab"}]}))
    assert run(FabricBuilder(nb, "example").options())["models"].keys() == {MODEL, "CE6881H-48T6CQ", "CE6881H-48T6CQ-K"}


def test_a_centralized_fabric_needs_its_gateway_pair():
    path = ROOT / "seed" / "fabrics" / "example-central-spines.yaml"
    settings, batch = wizard(path, name="gui2")
    no_gateway = {**batch, "spine_pairs": [{**p, "gateway": False} for p in batch["spine_pairs"]]}
    nb = FakeNetBox()
    with pytest.raises(SegmentError, match="needs its gateway pair"):
        run(create(nb, settings, no_gateway))
    assert not [s for s in nb.objects.get("dcim/sites", {}).values() if s["slug"] == "gui2"]   # nothing written
    run(create(nb, settings, batch))
    assert context(nb, "gui2")["fabric"]["gateway_pair"] == sorted(batch["spine_pairs"][0]["spines"])


def test_a_border_leaf_pair_as_gateway_pair():
    settings, batch = wizard(ROOT / "seed" / "fabrics" / "example-central.yaml", name="gui3")
    assert any(p["gateway"] for p in batch["pairs"])
    nb = FakeNetBox()
    run(create(nb, settings, batch))
    pair = next(p for p in batch["pairs"] if p["gateway"])
    assert context(nb, "gui3")["fabric"]["gateway_pair"] == sorted(pair["leaves"])


@pytest.mark.parametrize("change, why", [
    ({"asn": 4200000101}, "needs a 2-byte ASN"),
    ({"name": "example"}, "already exists"),
    ({"name": "Inventory"}, "reserved"),
    ({"name": "bad/name"}, "fabric name"),
    ({"prefixes": {"loopback": "10.0.0.0/24", "vtep": "10.0.0.0/25", "p2p": "10.1.0.0/24",
                   "mlag_bypass": "10.2.0.0/24", "gateways": "10.100.0.0/16"}}, "overlaps"),
    ({"prefixes": {"loopback": "8.8.8.0/24", "vtep": "10.0.1.0/24", "p2p": "10.1.0.0/24",
                   "mlag_bypass": "10.2.0.0/24", "gateways": "10.100.0.0/16"}}, "not private"),
    ({"gateway_mac": "0100-5e00-0101"}, "multicast"),
])
def test_new_fabric_refusals(change, why):
    nb = FakeNetBox()
    run(seed(nb, load(ROOT / "seed" / "fabrics" / "example.yaml")))
    settings, batch = wizard(ROOT / "seed" / "fabrics" / "example.yaml", name="gui4")
    batch = {**batch, "spines": [{**s, "name": f"g-{s['name']}"} for s in batch["spines"]], "pairs": [],
             "links": []}
    with pytest.raises(SegmentError, match=why):
        run(create(nb, {**settings, **change}, batch))


def test_switch_names_are_checked_against_every_fabric():
    nb = FakeNetBox()
    run(seed(nb, load(ROOT / "seed" / "fabrics" / "example.yaml")))
    settings, batch = wizard(ROOT / "seed" / "fabrics" / "example.yaml", name="gui5")
    with pytest.raises(SegmentError, match="already used"):
        run(create(nb, settings, batch))
    assert "gui5" not in {s["slug"] for s in nb.objects["dcim/sites"].values()}


# -- inventory -----------------------------------------------------------------------------

@pytest.fixture
def example() -> FakeNetBox:
    netbox = FakeNetBox()
    run(seed(netbox, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    return netbox


def test_inventory_add_list_update_remove(example):
    stock = Inventory(example)
    run(stock.add("2102351ABC", MODEL, "AA-BB-CC-00-11-22"))
    run(stock.add("2102351ABD", MODEL, None))
    assert run(stock.list()) == [
        {"serial": "2102351ABC", "name": "inv-2102351ABC", "model": MODEL, "mac": "aa:bb:cc:00:11:22"},
        {"serial": "2102351ABD", "name": "inv-2102351ABD", "model": MODEL, "mac": None}]
    for args, why in ((("2102351ABC", MODEL, None), "already in the inventory"),
                      (("X1", "CE9999", None), "no known port layout"),
                      (("X2", MODEL, "aabb.cc00.1122"), "already belongs"),
                      (("bad serial", MODEL, None), "serial number"),
                      (("X3", MODEL, "not-a-mac"), "not a MAC")):
        with pytest.raises(SegmentError, match=why):
            run(stock.add(*args))
    assert run(stock.update("2102351ABD", MODEL, "aa:bb:cc:00:11:33"))["mac"] == "aa:bb:cc:00:11:33"
    run(stock.remove("2102351ABD"))
    assert [e["serial"] for e in run(stock.list())] == ["2102351ABC"]


def test_a_switch_picked_from_the_inventory_takes_its_serial_and_mac(example):
    stock = Inventory(example)
    run(stock.add("SN-SPINE2", MODEL, "aa:bb:cc:00:11:22"))
    with pytest.raises(SegmentError, match="picked twice"):
        run(FabricBuilder(example, "example").add({"spines": [{"name": "spine2", "model": MODEL, "inventory": "SN-SPINE2"},
                                                      {"name": "spine3", "model": MODEL, "inventory": "SN-SPINE2"}]}))
    with pytest.raises(SegmentError, match="no switch with serial"):
        run(FabricBuilder(example, "example").add({"spines": [{"name": "spine2", "model": MODEL, "inventory": "SN-NONE"}]}))
    run(FabricBuilder(example, "example").add({
        "spines": [{"name": "spine2", "model": MODEL, "inventory": "SN-SPINE2"}],
        "links": [{"spine": "spine2", "spine_port": "100GE1/0/1", "leaf": "leaf1", "leaf_port": "100GE1/0/3"}]}))
    spine = devices(example, "example")["spine2"]
    assert spine["serial"] == "SN-SPINE2" and spine["custom_fields"]["meth_mac"] == "aa:bb:cc:00:11:22"
    assert run(stock.list()) == []


def test_api_new_fabric_and_inventory(example):
    accounts = FakeAccounts()
    app.dependency_overrides[get_netbox] = lambda: example
    app.dependency_overrides[get_accounts] = lambda: accounts
    app.dependency_overrides[_intent_unfrozen] = lambda: None
    try:
        client = TestClient(app)
        plan = client.post("/fabrics/address-plan", json={"supernet": "10.92.0.0/16"}).json()
        assert plan["prefixes"]["gateways"] == "10.92.128.0/17" and plan["gateway_mac"] == "0200-00fa-0001"
        assert MODEL in plan["models"]
        assert client.get("/inventory/models").json() == [MODEL, "CE6881H-48T6CQ", "CE6881H-48T6CQ-K", "vEOS-lab"]   # imported fabrics
        assert MODEL not in client.post("/fabrics/address-plan", json={"supernet": "10.92.0.0/16", "vendor": "arista"}).json()["models"]
        assert client.post("/inventory", json={"serial": "SN1", "model": MODEL, "mac": "aa:bb:cc:00:00:01"}).status_code == 201
        settings = {"name": "dc9", "asn": 65009, "prefixes": plan["prefixes"], "gateway_mac": plan["gateway_mac"]}
        switches = {"spines": [{"name": "dc9-spine1", "model": MODEL, "inventory": "SN1"}],
                    "pairs": [{"leaves": ["dc9-leaf1", "dc9-leaf2"], "model": MODEL,
                               "peer_link_members": ["100GE1/0/5", "100GE1/0/6"]}],
                    "links": [{"spine": "dc9-spine1", "spine_port": "100GE1/0/1", "leaf": "dc9-leaf1", "leaf_port": "100GE1/0/1"},
                              {"spine": "dc9-spine1", "spine_port": "100GE1/0/2", "leaf": "dc9-leaf2", "leaf_port": "100GE1/0/1"}]}
        response = client.post("/fabrics", json={"settings": settings, "switches": switches})
        assert response.status_code == 201, response.text
        assert response.json()["site"] == "dc9"
        assert client.get("/inventory").json() == []
        assert "dc9" in [f["site"] for f in client.get("/fabrics").json()]
        assert client.post("/fabrics", json={"settings": settings, "switches": switches}).status_code == 400
        client.post("/inventory", json={"serial": "SN2", "model": MODEL})
        assert "inv-SN2" not in [d["name"] for d in client.get("/devices").json()]   # not in any fabric
        assert "inventory" not in [f["site"] for f in client.get("/fabrics").json()]
        assert client.delete("/inventory/SN2").status_code == 200
    finally:
        for dep in (get_netbox, get_accounts, _intent_unfrozen):
            app.dependency_overrides.pop(dep, None)
    assert [a for _, a, _ in accounts.events] == ["inventory.add", "fabric.create", "inventory.add", "inventory.remove"]
