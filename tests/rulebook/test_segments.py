"""Segment patches: attach, detach, change vid, tagged to untagged."""

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

BASE = "interface Nve1\n source 10.10.1.1\n"
BD = """\
bridge-domain 500
 vxlan vni 10500
 evpn
  route-distinguisher auto
  vpn-target 65001:10500 export-extcommunity
  vpn-target 65001:10500 import-extcommunity
"""
NVE = BASE + " vni 10500 head-end peer-list protocol bgp\n"
TAGGED = "interface Eth-Trunk1.500 mode l2\n encapsulation dot1q vid 500\n bridge-domain 500\n"
UNTAGGED = "interface Eth-Trunk1.1 mode l2\n encapsulation untag\n bridge-domain 500\n"
ATTACHED = BD + NVE + TAGGED


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def test_attach_creates_bridge_domain_then_vni_then_subinterface():
    lines = patch(BASE, ATTACHED).splitlines()

    assert lines.index("bridge-domain 500") < lines.index(" vni 10500 head-end peer-list protocol bgp")
    assert lines.index(" vni 10500 head-end peer-list protocol bgp") < lines.index("interface Eth-Trunk1.500 mode l2")


def test_detach_removes_in_reverse_order():
    assert patch(ATTACHED, BASE).splitlines() == [
        "undo interface Eth-Trunk1.500",
        "interface Nve1",
        " undo vni 10500 head-end peer-list protocol bgp",
        " quit",
        "undo bridge-domain 500",
    ]


def test_vid_change_undoes_the_old_encapsulation_first():
    assert patch(ATTACHED, ATTACHED.replace("vid 500", "vid 501")).splitlines() == [
        "interface Eth-Trunk1.500 mode l2",
        " undo encapsulation dot1q vid 500",
        " encapsulation dot1q vid 501",
        " quit",
    ]


def test_tagged_to_untagged_replaces_the_subinterface():
    assert patch(ATTACHED, BD + NVE + UNTAGGED).splitlines() == [
        "undo interface Eth-Trunk1.500",
        "interface Eth-Trunk1.1 mode l2",
        " encapsulation untag",
        " bridge-domain 500",
        " quit",
    ]


def test_unchanged_segment_needs_no_patch():
    assert patch(ATTACHED, ATTACHED) == ""


def test_a_sub_interface_changing_bridge_domain_leaves_the_old_one_without_its_number():
    """Segment test-seg-1 (BD 1) replaced by web (BD 500) untagged on one port, in one deploy:
    V300 refuses `undo bridge-domain 1` in the sub-interface and joining a second bridge domain
    (leaf1); `undo bridge-domain` then `bridge-domain 500` is accepted, also after
    the old bridge domain's removal earlier in the same candidate."""
    old = "bridge-domain 1\n vxlan vni 10001\ninterface 10GE1/0/3.1 mode l2\n encapsulation untag\n bridge-domain 1\n"
    new = "bridge-domain 500\n vxlan vni 10500\ninterface 10GE1/0/3.1 mode l2\n encapsulation untag\n bridge-domain 500\n"
    result = [line.strip() for line in patch(old, new).splitlines() if line.strip() and line.strip() != "quit"]
    sub = result.index("interface 10GE1/0/3.1 mode l2")
    assert result[sub + 1:sub + 3] == ["undo bridge-domain", "bridge-domain 500"]
    assert "undo bridge-domain 1" in result[:sub]   # the bridge domain itself, as Annet orders it


def test_a_vlan_trunk_becoming_a_segment_port_drops_its_vlans_before_the_sub_interface():
    # VRP refuses `encapsulation dot1q vid <n>` while the trunk passes VLAN <n>.
    trunk = "interface Eth-Trunk1\n mode lacp-static\n dfs-group 1 m-lag 1\n"
    vlan_trunk = "interface Eth-Trunk1\n port link-type trunk\n port trunk allow-pass vlan 2 to 4094\n" \
                 " mode lacp-static\n dfs-group 1 m-lag 1\n"
    lines = patch(BD + NVE + vlan_trunk, BD + NVE + trunk + TAGGED).splitlines()
    assert lines.index(" undo port link-type") < lines.index("interface Eth-Trunk1.500 mode l2")
