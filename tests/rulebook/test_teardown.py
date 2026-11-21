"""Removal of M-LAG/VXLAN config: our ordering and NVE rules.

Each step must remove what the next one depends on. WIPE_ACL owns a leaf's whole fabric
config and nothing of its management plane; Annet's patch with it removes all of that.
"""

import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from annet.annlib.netdev.views.hardware import HardwareView
from annet.annlib.rbparser.acl import compile_acl_text
from annet.api import _diff_and_patch, _format_patch_blocks
from annet.vendors import registry_connector, tabparser

from tests.rulebook.patch import MODEL_HW, patch, use_our_rulebook

FIXTURE = (Path(__file__).parent / "fixtures" / "mlag-leaf-v300.cfg").read_text()

# Annet protects every "interface" ACL line from removal by default (cant_delete);
# logical interfaces are meant to go, physical ports only lose their trunk settings.
WIPE_ACL = """
dfs-group ~
evpn-overlay enable
stp v-stp enable
vlan batch ~
vlan */\\d+/
bridge-domain *
interface */Vlanif\\d+/  %cant_delete=0
interface */Eth-Trunk\\d+(\\.\\d+)?/ ~  %cant_delete=0
interface */Eth-Trunk\\d+(\\.\\d+)?/  %cant_delete=0
interface */LoopBack\\d+/  %cant_delete=0
interface */Nve\\d+/  %cant_delete=0
interface */(10|25|40|100)GE\\d+\\/\\d+\\/\\d+/
    eth-trunk ~
    port link-type ~
    undo port hybrid untagged vlan 1
ip route-static */\\d+\\.\\d+\\.\\d+\\.\\d+/ ~
"""


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def wipe(config: str) -> list[str]:
    """The patch Annet builds with WIPE_ACL and nothing generated, for this running config."""
    hw = HardwareView(MODEL_HW, "")
    device = SimpleNamespace(hostname="leaf1", hw=hw)
    old = tabparser.parse_to_tree(config, registry_connector.get().match(hw).make_formatter().split)
    acl = compile_acl_text(WIPE_ACL, hw.vendor)
    _, tree = _diff_and_patch(device, old, {}, acl, None, False)
    return _format_patch_blocks(tree, hw, " ").splitlines()


def position(lines: list[str], command: str) -> int:
    return lines.index(command)


def test_nve_interface_is_deleted_not_emptied():
    assert patch("interface Nve1\n source 10.0.0.1\n", "").splitlines() == ["undo interface Nve1"]


def test_physical_port_is_never_deleted():
    result = patch("interface 10GE1/0/1\n eth-trunk 1\n", "").splitlines()

    assert "undo interface 10GE1/0/1" not in result
    assert result == ["interface 10GE1/0/1", " undo eth-trunk", " quit"]


def test_l2_subinterface_is_deleted_without_mode_suffix():
    old = "interface Eth-Trunk1.500 mode l2\n encapsulation dot1q vid 500\n bridge-domain 500\n"

    assert patch(old, "").splitlines() == ["undo interface Eth-Trunk1.500"]


def test_teardown_order():
    lines = wipe(FIXTURE)
    order = [
        "undo interface Eth-Trunk1.500",   # sub-interface references the bridge domain
        "interface 10GE1/0/1",             # members leave their trunks
        "undo interface Nve1",             # NVE references bridge domains and loopbacks
        "undo interface Eth-Trunk0",       # peer-link
        "undo interface Eth-Trunk1",       # dfs-group 1 m-lag 1
        "undo dfs-group 1",
        "undo stp v-stp enable",
        "undo interface LoopBack1",        # the DFS group's source address
        "undo bridge-domain 500",
        "undo evpn-overlay enable",
    ]

    positions = [position(lines, command) for command in order]

    assert positions == sorted(positions), "\n".join(lines)


def test_wipe_never_touches_the_management_plane_or_aaa():
    text = "\n".join(wipe(FIXTURE))

    for kept in ("MEth0/0/0", "_management_vpn_", "aaa", "local-user", "snmp-agent", "lldp", "netconf",
                 "stp mode", "device transceiver"):
        assert kept not in text, kept
    assert "undo ip route-static 10.1.2.1 255.255.255.255 192.168.20.1" in text


def test_removing_m6_from_a_leaf():
    """Removing what the overlay generators own from a leaf, computed from its generated config:
    the union of their ACLs with nothing generated."""
    from generators.bgp_evpn import BgpEvpn
    from generators.mlag import Mlag
    from generators.mlag_bypass import MlagBypass
    from generators.nve import Nve
    from generators.vlans import Vlans
    from tests.generators.harness import devices_from_fabric, render

    device = {d.hostname: d for d in devices_from_fabric("tests/fixtures/example-small.yaml")}["leaf1"]
    old = tabparser.parse_to_tree(render(device), registry_connector.get().match(device.hw).make_formatter().split)
    overlay = "\n".join(gen(device.storage).acl_huawei(device) for gen in (Mlag, MlagBypass, Vlans, Nve, BgpEvpn))
    acl = compile_acl_text(textwrap.dedent(overlay), device.hw.vendor)
    _, tree = _diff_and_patch(device, old, {}, acl, None, False)

    assert _format_patch_blocks(tree, device.hw, " ").splitlines() == [
        "undo bgp",
        "commit",
        "undo bgp",
        "undo ip route-static 10.10.0.3 255.255.255.255 10.12.0.2",
        "undo interface Vlanif4093",
        "interface 100GE1/0/5", " undo eth-trunk", " quit",
        "interface 100GE1/0/6", " undo eth-trunk", " quit",
        "interface 10GE1/0/1", " undo eth-trunk", " quit",
        "interface 10GE1/0/2", " undo eth-trunk", " quit",
        "undo interface Nve1",
        "undo interface Eth-Trunk0",
        "undo interface Eth-Trunk1",
        "undo vlan 4093",
        "undo vlan batch 4093",
        "undo dfs-group 1",
        "undo stp v-stp enable",
        "undo evpn-overlay enable",
    ]
