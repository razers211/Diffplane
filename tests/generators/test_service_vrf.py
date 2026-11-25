"""Services in an existing VPN instance: another service's, which keeps the RD
of the service that made it and outlives it, or a routed segment's, whose own firewall then gives
it a plain default route."""

from functools import cache
from pathlib import Path

from tests.generators.harness import devices_from_fabric, patch_to, render
from tests.generators.test_golden import example_segments, nb_segments_extra

EXAMPLE = Path(__file__).resolve().parents[2] / "seed" / "fabrics" / "example.yaml"


async def shared(nb):
    from orchestrator.routing import Routing

    await example_segments(nb)
    await nb_segments_extra(nb)
    routing = Routing(nb)
    await routing.create_service("example", "FW", "leaf1", "10GE1/0/4", 3000, "10.255.0.1/30", "10.255.0.2", 65100)
    await routing.create_service("example", "ISP", "leaf1", "10GE1/0/5", 3002, "10.255.2.1/30", "10.255.2.2",
                                 kind="isp", vrf="FW")
    await routing.create_service("example", "FW2", "leaf1", "10GE1/0/7", 3006, "10.255.6.1/30", "10.255.6.2", 65300,
                                 vrf="FW")
    await routing.set_routing("example", 10500, True)                    # web behind FW
    await routing.set_routing("example", 10600, True, firewall=None)     # db
    await routing.create_service("example", "INSIDE", "leaf1", "10GE1/0/6", 3004, "10.255.4.1/30", "10.255.4.2",
                                 65200, vrf="db-600")
    await routing.set_routing("example", 10600, True, firewall="INSIDE")  # db's exit: the firewall in its own instance


async def creator_gone(nb):
    from orchestrator.routing import Routing

    await shared(nb)
    await Routing(nb).set_routing("example", 10500, True, firewall=None)
    await Routing(nb).delete_service("example", "FW")


@cache
def leaf(prepare):
    return {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=prepare)}["leaf1"]


def block(config: str, head: str) -> str:
    """The block under `head` (a top-level line), as rendered."""
    lines = config.splitlines()
    start = lines.index(head)
    end = next((i for i in range(start + 1, len(lines)) if not lines[i].startswith(" ")), len(lines))
    return "\n".join(lines[start:end])


def test_a_shared_instance_is_written_once_with_its_makers_rd():
    config = render(leaf(shared))   # also checks no two generators own a line (AclNotExclusiveError)
    assert config.count("ip vpn-instance FW\n") == 1
    assert "route-distinguisher 10.0.0.3:3000" in block(config, "ip vpn-instance FW")
    assert "ip vpn-instance ISP" not in config
    assert "ip binding vpn-instance FW" in block(config, "interface Vlanif3002")
    bgp = block(config, "bgp 65001")
    assert bgp.count("  vpn-instance FW\n") == 1 and bgp.count("  ipv4-family vpn-instance FW\n") == 1
    assert "peer 10.255.0.2 as-number 65100" in bgp and "peer 10.255.6.2 as-number 65300" in bgp
    assert patch_to(render(leaf(shared)), leaf(shared)) == ""


def test_a_firewall_in_a_segments_instance_gives_it_a_plain_default_route():
    config = render(leaf(shared))
    assert "ip vpn-instance INSIDE" not in config
    assert "ip binding vpn-instance db-600" in block(config, "interface Vlanif3004")
    assert "ip route-static vpn-instance db-600 0.0.0.0 0.0.0.0 10.255.4.2" in config.splitlines()
    assert "vpn-instance db-600 0.0.0.0 0.0.0.0 vpn-instance" not in config
    bgp = block(config, "bgp 65001")
    assert "  vpn-instance db-600\n    peer 10.255.4.2 as-number 65200\n    peer 10.255.4.2 bfd enable" in bgp
    lines = bgp.splitlines()
    start = lines.index("  ipv4-family vpn-instance db-600")
    family = []
    for line in lines[start + 1:]:
        if not line.startswith("    "):
            break
        family.append(line.strip())
    assert set(family) == {"import-route direct", "import-route static", "peer 10.255.4.2 enable", "advertise l2vpn evpn"}


def test_the_instance_outlives_its_maker_and_only_its_session_goes():
    before, after = leaf(shared), leaf(creator_gone)
    config = render(after)
    assert "route-distinguisher 10.0.0.3:3000" in block(config, "ip vpn-instance FW")   # the maker's RD, kept
    assert after.config_context["routing"]["retired_services"] == {"leaf1": ["FW 10.255.0.2"]}
    lines = patch_to(render(before), after).splitlines()
    assert "undo interface Vlanif3000" in lines
    assert any(line.strip() == "undo peer 10.255.0.2" for line in lines), "\n".join(lines)   # FW2 keeps the BGP block
    assert not any(line.strip() in ("undo ip vpn-instance FW", "undo vpn-instance FW") for line in lines), lines
    assert patch_to(render(after), after) == ""


async def across_switches(nb):
    """A second ISP link on the other leaf of the pair joins the first one's instance."""
    from orchestrator.routing import Routing

    await shared(nb)
    await Routing(nb).create_service("example", "ISP_B", "leaf2", "10GE1/0/5", 3008, "10.255.8.1/30", "10.255.8.2",
                                     kind="isp", vrf="FW")


def test_another_switch_gets_the_shared_instance_with_its_own_rd():
    devices = {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=across_switches)}
    second = devices["leaf2"]
    config = render(second)
    own = next(i for i in second.interfaces if i.name == "LoopBack0").ip_addresses[0].address.split("/")[0]
    assert own != "10.0.0.3"
    assert f"route-distinguisher {own}:3000" in block(config, "ip vpn-instance FW")   # its loopback, the maker's VLAN
    assert "ip binding vpn-instance FW" in block(config, "interface Vlanif3008")
    assert "ip vpn-instance ISP_B" not in config
    assert patch_to(render(second), second) == ""
    first = render(devices["leaf1"])
    assert "route-distinguisher 10.0.0.3:3000" in block(first, "ip vpn-instance FW")   # unchanged there
    assert "Vlanif3008" not in first
