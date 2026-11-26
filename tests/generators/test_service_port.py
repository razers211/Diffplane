"""Several services on one port: each in its own VLAN, the port a trunk
passing all of them; deleting one leaves the others' VLANs on the port."""

from functools import cache
from pathlib import Path

import pytest

from tests.generators.harness import devices_from_fabric, patch_to, render
from tests.generators.test_golden import example_segments

EXAMPLE = Path(__file__).resolve().parents[2] / "seed" / "fabrics" / "example.yaml"


async def two(nb):
    from orchestrator.routing import Routing

    await example_segments(nb)
    routing = Routing(nb)
    await routing.create_service("example", "ISP-A", "leaf1", "10GE1/0/4", 3000, "10.255.0.1/30", "10.255.0.2", kind="isp")
    await routing.create_service("example", "ISP-B", "leaf1", "10GE1/0/4", 3002, "10.255.2.1/30", "10.255.2.2", kind="isp")


async def one(nb):
    from orchestrator.routing import Routing

    await two(nb)
    await Routing(nb).delete_service("example", "ISP-B")


@cache
def leaf(prepare):
    return {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=prepare)}["leaf1"]


def test_a_port_carries_several_services():
    config = render(leaf(two))
    assert "interface 10GE1/0/4\n  port link-type trunk\n  port trunk allow-pass vlan 3000 3002" in config
    assert "interface Vlanif3000" in config and "interface Vlanif3002" in config
    assert patch_to(config, leaf(two)) == ""


def test_deleting_one_service_keeps_the_others_vlan_on_the_port():
    lines = [line.strip() for line in patch_to(render(leaf(two)), leaf(one)).splitlines()]
    assert "undo interface Vlanif3002" in lines, lines
    assert "undo port link-type" not in lines and "undo port trunk allow-pass vlan all" not in lines, lines
    assert any(line.startswith("undo port trunk allow-pass vlan 3002") for line in lines), lines


def test_the_same_vlan_twice_on_a_switch_is_refused():
    import asyncio

    from orchestrator.routing import Routing
    from orchestrator.segments import SegmentError
    from tests.collector.test_collector import example_netbox

    nb = example_netbox()
    asyncio.run(example_segments(nb))
    routing = Routing(nb)
    asyncio.run(routing.create_service("example", "ISP-A", "leaf1", "10GE1/0/4", 3000, "10.255.0.1/30", "10.255.0.2", kind="isp"))
    with pytest.raises(SegmentError, match="VLAN 3000 is already used by service ISP-A"):
        asyncio.run(routing.create_service("example", "ISP-B", "leaf1", "10GE1/0/4", 3000, "10.255.2.1/30", "10.255.2.2",
                                           kind="isp"))
