"""Border leaves of a symmetric fabric: they only route. Every routed
segment's VPN instance (L3 VNI, L3 route target, `advertise l2vpn evpn`) exists on them as soon
as the segment does; no bridge domain, VBDIF or attachment. Ordinary leaves get a segment only
where its ports are attached."""

from functools import cache
from pathlib import Path

import yaml

from tests.generators.harness import devices_from_fabric, render

EXAMPLE = Path(__file__).resolve().parents[2] / "seed" / "fabrics" / "example.yaml"


async def tenant(nb):
    """web attached on leaf1's trunk behind the firewall on border leaf3; db routed, attached nowhere."""
    from orchestrator.routing import Routing
    from orchestrator.segments import Segments

    segments, routing = Segments(nb), Routing(nb)
    await segments.create("example", "web", 10500, 500)
    await segments.create("example", "db", 10600, 600)
    await segments.attach("example", "leaf1", "Eth-Trunk1", 10500, 500)
    await routing.create_service("example", "FW", "leaf3", "10GE1/0/4", 3000, "10.255.0.1/30", "10.255.0.2", 65100)
    await routing.set_routing("example", 10500, True)
    await routing.set_routing("example", 10600, True, firewall=None)


@cache
def devices() -> dict:
    import tempfile

    data = yaml.safe_load(EXAMPLE.read_text())
    data["leaf_pairs"][1]["role"] = "border-leaf"
    path = Path(tempfile.mkdtemp()) / "example.yaml"
    path.write_text(yaml.safe_dump(data))
    return {d.hostname: d for d in devices_from_fabric(path, prepare=tenant)}


def config(name: str) -> str:
    return render(devices()[name])


def test_border_leaves_have_every_routed_segment_as_routing_only():
    for border in ("leaf3", "leaf4"):
        text = config(border)
        for bd, l3, vpn in ((500, 1010500, "web-500"), (600, 1010600, "db-600")):   # db is attached nowhere
            assert f"ip vpn-instance {vpn}\n" in text, (border, bd)
            assert f"  vxlan vni {l3}\n" in text
            assert f"vpn-target 65001:{l3} export-extcommunity evpn" in text
            assert f"  vpn-instance {vpn}\n" in text and f"  ipv4-family vpn-instance {vpn}\n" in text
        for absent in ("bridge-domain 500", "bridge-domain 600", "Vbdif", "import-route direct", "mode l2"):
            assert absent not in text, (border, absent)
        assert text.count("advertise l2vpn evpn") == 2


def test_the_firewall_border_leaf_advertises_the_default_route():
    leaf3, leaf4 = config("leaf3"), config("leaf4")
    assert "ip route-static vpn-instance web-500 0.0.0.0 0.0.0.0 vpn-instance FW 10.255.0.2" in leaf3
    assert "  ipv4-family vpn-instance web-500\n    import-route static\n" in leaf3
    assert "import-route static" not in leaf4.split("ipv4-family vpn-instance web-500")[1].split("ipv4-family")[0]
    assert "ip vpn-instance FW" not in leaf4   # a service on a physical port is that leaf's alone


def test_ordinary_leaves_get_a_segment_only_where_attached():
    leaf1, leaf2 = config("leaf1"), config("leaf2")
    assert "interface Vbdif500" in leaf1 and "ip vpn-instance web-500" in leaf1
    assert "ip vpn-instance db-600" not in leaf1   # db is attached nowhere
    assert "interface Vbdif500" in leaf2            # web's trunk is an M-LAG trunk: both leaves
