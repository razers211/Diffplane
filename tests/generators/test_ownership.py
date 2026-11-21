"""Line ownership split by object.

Containers (VPN instances, BGP-VPN instances and families, Vlanifs, port settings) stay
owned by kind. Routing objects (static routes, BGP peers, ...) are owned only when the
platform generated them, or generated them before and they are retired: hand-made ones are
never removed, and a deleted service still takes its routes with it.
"""

from functools import cache
from pathlib import Path

from tests.generators.harness import devices_from_fabric, patch_to, render
from tests.generators.test_golden import example_routing

EXAMPLE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "example-small.yaml"


@cache
def routed():
    return {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=example_routing)}


def insert_after(config: str, anchor: str, lines: str) -> str:
    assert anchor in config, anchor
    return config.replace(anchor, anchor + lines, 1)


def with_hand_made(config: str) -> str:
    """leaf1's generated config plus routing objects someone configured by hand."""
    config = insert_after(config, "bgp 65001\n", "  peer 192.0.2.1 as-number 65100\n")
    config = insert_after(config, "  vpn-instance web-500\n", "    peer 192.0.2.9 as-number 65300\n")
    config = insert_after(config, "  ipv4-family vpn-instance web-500\n", "    peer 192.0.2.9 enable\n")
    return config + (
        "ip route-static 10.250.0.0 255.255.255.0 NULL0\n"
        "ip route-static vpn-instance FW 10.9.0.0 255.255.0.0 10.255.0.2\n"
        "ip route-static vpn-instance web-500 10.8.0.0 255.255.0.0 NULL0\n"
    )


def test_the_generated_lab_config_needs_no_patch():
    leaf = routed()["leaf1"]
    assert patch_to(render(leaf), leaf) == ""


def test_hand_made_routing_objects_are_never_removed():
    leaf = routed()["leaf1"]
    patch = patch_to(with_hand_made(render(leaf)), leaf)
    assert "undo" not in patch, patch


def test_a_departed_fabric_peer_is_removed_but_not_a_foreign_one():
    leaf = routed()["leaf1"]
    running = insert_after(render(leaf), "bgp 65001\n",
                           "  peer 10.10.0.99 as-number 65001\n  peer 192.0.2.1 as-number 65100\n")
    lines = patch_to(running, leaf).splitlines()
    assert " undo peer 10.10.0.99 as-number 65001" in lines or " undo peer 10.10.0.99" in lines, lines
    assert not any("192.0.2.1" in line for line in lines)


async def delete_everything_routed(nb):
    """The example tenant, then everything routed and the FW service deleted through the API."""
    from orchestrator.routing import Routing

    await example_routing(nb)
    routing = Routing(nb)
    for vni in (10500, 10600, 10700):
        await routing.set_routing("example", vni, False)
    await routing.delete_service("example", "FW")


def test_deleting_a_service_still_removes_its_routes_and_session():
    after = {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=delete_everything_routed)}["leaf1"]
    assert after.config_context["routing"]["retired_services"] == {"leaf1": ["FW 10.255.0.2"]}

    lines = patch_to(with_hand_made(render(routed()["leaf1"])), after).splitlines()

    for removal in ("undo ip route-static vpn-instance FW 10.100.0.0 255.255.255.0 vpn-instance web-500",
                    "undo ip route-static vpn-instance web-500 0.0.0.0 0.0.0.0 vpn-instance FW 10.255.0.2",
                    "undo ip vpn-instance FW", " undo vpn-instance FW", "undo interface Vlanif3000"):
        assert removal in lines, "\n".join(lines)
    # Hand-made routes in the global table survive; those inside a deleted instance go with it.
    assert not any("10.250.0.0" in line or "192.0.2.1 " in line for line in lines)


async def firewall_without_asn(nb):
    from orchestrator.routing import Routing

    await example_routing(nb)
    await Routing(nb).set_service_asn("example", "FW", None)


def test_clearing_the_firewall_asn_removes_its_session():
    after = {d.hostname: d for d in devices_from_fabric(EXAMPLE, prepare=firewall_without_asn)}["leaf1"]
    lines = patch_to(render(routed()["leaf1"]), after).splitlines()
    assert " undo vpn-instance FW" in lines or "  undo peer 10.255.0.2" in lines, "\n".join(lines)
    assert "undo ip vpn-instance FW" not in lines   # the service itself stays


def test_a_management_bridge_domain_is_left_alone():
    """An imported fabric's BD 990: recorded by the import, never generated or removed."""
    import copy

    leaf = copy.copy(routed()["leaf1"])
    leaf.config_context = copy.deepcopy(leaf.config_context)
    leaf.config_context.setdefault("overrides", {}).setdefault("devices", {})["leaf1"] = {"management": {
        "bridge_domains": [990], "vnis": [100990], "vpn_instances": ["OOB-SW"], "vlans": [990],
        "interfaces": ["10GE1/0/9.110"]}}
    running = render(leaf).replace("vlan batch 4093", "vlan batch 990 4093")
    running = insert_after(running, "interface Nve1\n", "  vni 100990 head-end peer-list protocol bgp\n")
    running += ("ip vpn-instance OOB-SW\n  ipv4-family\n    route-distinguisher 990:990\n"
                "bridge-domain 990\n  l2 binding vlan 990\n  vxlan vni 100990\n"
                "interface Vbdif990\n  ip binding vpn-instance OOB-SW\n  ip address 10.150.99.1 255.255.255.0\n"
                "interface 10GE1/0/9.110 mode l2\n  encapsulation dot1q vid 990\n  bridge-domain 990\n")
    assert patch_to(running, leaf) == ""
    stray = running + "interface 10GE1/0/8.5 mode l2\n  encapsulation dot1q vid 5\n  bridge-domain 5\nbridge-domain 5\n"
    assert "undo interface 10GE1/0/8.5" in patch_to(stray, leaf)   # the rest stays owned by kind
    assert "undo bridge-domain 5" in patch_to(stray, leaf)
    # without the record they all go (ownership by kind), so the test does test something
    removals = patch_to(running, routed()["leaf1"])
    for line in ("undo bridge-domain 990", "undo interface Vbdif990", "undo ip vpn-instance OOB-SW",
                 "undo interface 10GE1/0/9.110"):
        assert line in removals, removals
