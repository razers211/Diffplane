"""A fabric is one vendor: its setting, its models, its manufacturer and its interface names."""

import asyncio
from pathlib import Path

import pytest
import yaml

from generators import naming
from seed import ports
from seed.loader import Seeder
from seed.model import FabricError, load, parse
from tests.seed.fake_netbox import FakeNetBox

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ARISTA = FIXTURES / "example-arista.yaml"
SMALL = FIXTURES / "example-small.yaml"


def test_a_fabric_is_huawei_unless_it_names_its_vendor():
    assert load(SMALL).vendor == naming.HUAWEI
    assert "vendor" not in load(SMALL).services.config_context()["fabric"]   # absent: Huawei
    fabric = load(ARISTA)
    assert fabric.vendor == naming.ARISTA
    assert fabric.services.config_context()["fabric"]["vendor"] == "arista"
    pair = fabric.leaf_pairs[0]
    assert pair.peer_link_name == "Port-Channel10" and pair.mlag_trunks[0].name == "Port-Channel1"


def test_a_fabric_takes_its_vendors_models_only():
    data = yaml.safe_load(ARISTA.read_text())
    data["spines"][0]["model"] = "CE6881H-48S6CQ"
    with pytest.raises(FabricError, match="arista fabric takes arista switches only: CE6881H-48S6CQ"):
        parse(data)
    data = yaml.safe_load(SMALL.read_text())
    data["spines"][0]["model"] = "vEOS-lab"
    with pytest.raises(FabricError, match="huawei fabric takes huawei switches only: vEOS-lab"):
        parse(data)
    data["fabric"]["vendor"] = "juniper"
    with pytest.raises(FabricError, match="fabric.vendor must be one of"):
        parse(data)


def test_veos_lab_is_an_assumed_arista_model_in_port_order():
    assert ports.vendor_of_model("vEOS-lab") == naming.ARISTA and "vEOS-lab" in ports.ASSUMED
    assert ports.vendor_of_model("CE6881H-48S6CQ") == naming.HUAWEI
    assert ports.known_models(naming.ARISTA) == ["vEOS-lab"]
    assert "vEOS-lab" not in ports.known_models(naming.HUAWEI)
    assert ports.ports_of("vEOS-lab")[:3] == ["Ethernet1", "Ethernet2", "Ethernet3"]
    assert sorted(["Ethernet10", "Ethernet2"], key=ports._port_key) == ["Ethernet2", "Ethernet10"]


def test_an_arista_fabric_seeds_arista_device_types_and_eos_names():
    nb = FakeNetBox()
    asyncio.run(Seeder(nb, load(ARISTA)).run())
    makers = {m["id"]: m["name"] for m in nb.objects["dcim/manufacturers"].values()}
    types = list(nb.objects["dcim/device-types"].values())
    assert [(t["model"], makers[t["manufacturer"]]) for t in types] == [("vEOS-lab", "Arista")]
    leaf = next(d for d in nb.objects["dcim/devices"].values() if d["name"] == "eos-leaf1")
    names = {i["name"] for i in nb.objects["dcim/interfaces"].values() if i["device"] == leaf["id"]}
    assert {"Ethernet1", "Ethernet8", "Loopback0", "Loopback1", "Port-Channel10", "Port-Channel1", "Vlan4093"} <= names
    assert not {n for n in names if n.startswith(("LoopBack", "Eth-Trunk", "Vlanif", "10GE"))}


def test_a_learned_models_vendor_is_its_netbox_manufacturer():
    nb = FakeNetBox()
    maker = asyncio.run(nb.create("dcim/manufacturers", {"name": "Arista", "slug": "arista"}))
    asyncio.run(nb.create("dcim/device-types", {"manufacturer": maker["id"], "model": "DCS-7050SX3-48YC8",
                                                "slug": "dcs-7050sx3-48yc8"}))
    try:
        asyncio.run(ports.refresh(nb))
        assert ports.vendor_of_model("DCS-7050SX3-48YC8") == naming.ARISTA
    finally:
        ports.LEARNED_VENDOR.clear()
