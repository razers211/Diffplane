"""Port layouts: bootstrap models and those learned from switches."""

import asyncio
from pathlib import Path

from seed import ports
from seed.model import load
from tests.helpers import pip_dc, seed
from tests.seed.fake_netbox import FakeNetBox

ROOT = Path(__file__).resolve().parents[2]


def test_the_copper_model_seeds_its_54_ports_typed_rj45_and_is_assumed():
    assert "CE6881H-48T6CQ-K" in ports.ASSUMED and ports.ASSUMED <= set(ports.BOOTSTRAP)
    nb = FakeNetBox()
    asyncio.run(seed(nb, pip_dc(copper=True)))
    leaf = next(d for d in asyncio.run(nb.find("dcim/devices")) if d["name"] == "p-leaf1")
    found = {i["name"]: i["type"]["value"] for i in asyncio.run(nb.find("dcim/interfaces", device_id=leaf["id"]))}
    assert set(ports.ports_of("CE6881H-48T6CQ-K")) <= set(found) and len(ports.ports_of("CE6881H-48T6CQ-K")) == 54
    assert found["10GE1/0/1"] == "10gbase-t" and found["100GE1/0/1"] == "100gbase-x-qsfp28"
    assert ports.is_copper(found["10GE1/0/1"]) and not ports.is_copper(found["100GE1/0/1"])


def test_a_learned_layout_comes_from_netbox_interface_templates_in_port_order():
    nb = FakeNetBox()

    async def go():
        m = await nb.create("dcim/manufacturers", {"name": "Huawei", "slug": "huawei"})
        t = await nb.create("dcim/device-types", {"manufacturer": m["id"], "model": "CE8850-64CQ-EI", "slug": "x"})
        for name, kind in (("100GE1/0/10", "100gbase-x-qsfp28"), ("10GE1/0/2", "10gbase-x-sfpp"),
                           ("100GE1/0/2", "100gbase-x-qsfp28")):
            await nb.create("dcim/interface-templates", {"device_type": t["id"], "name": name, "type": kind})
        await ports.refresh(nb)

    try:
        asyncio.run(go())
        assert ports.ports_of("CE8850-64CQ-EI") == ["10GE1/0/2", "100GE1/0/2", "100GE1/0/10"]
        assert "CE8850-64CQ-EI" in ports.known_models() and "CE6881H-48S6CQ" in ports.known_models()
    finally:
        ports.LEARNED.clear()
