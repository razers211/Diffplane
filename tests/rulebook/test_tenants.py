"""Tenant patches: a routed segment behind a firewall service, added and removed."""

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

BASE = """\
vlan batch 4093
bridge-domain 500
 vxlan vni 10500
 evpn
  route-distinguisher auto
  vpn-target 65001:10500 export-extcommunity
  vpn-target 65001:10500 import-extcommunity
bgp 65001
 peer 10.10.0.1 as-number 65001
"""
ROUTED = """\
ip vpn-instance web-500
 ipv4-family
  route-distinguisher 10.10.0.2:500
  vpn-target 65001:1010500 export-extcommunity evpn
  vpn-target 65001:1010500 import-extcommunity evpn
 vxlan vni 1010500
ip vpn-instance FW
 ipv4-family
  route-distinguisher 10.10.0.2:3000
vlan batch 3000 4093
interface Vlanif3000
 ip binding vpn-instance FW
 ip address 10.255.0.1 255.255.255.252
bridge-domain 500
 vxlan vni 10500
 evpn
  route-distinguisher auto
  vpn-target 65001:10500 export-extcommunity
  vpn-target 65001:10500 import-extcommunity
  vpn-target 65001:1010500 export-extcommunity
interface Vbdif500
 ip binding vpn-instance web-500
 ip address 10.100.0.1 255.255.255.0
 mac-address 0000-5e00-0101
 vxlan anycast-gateway enable
 arp collect host enable
interface 10GE1/0/4
 port link-type trunk
 port trunk allow-pass vlan 3000
ip route-static vpn-instance FW 10.100.0.0 255.255.255.0 vpn-instance web-500
ip route-static vpn-instance web-500 0.0.0.0 0.0.0.0 vpn-instance FW 10.255.0.2
bgp 65001
 peer 10.10.0.1 as-number 65001
 vpn-instance web-500
 ipv4-family vpn-instance web-500
  import-route direct
  import-route static
  advertise l2vpn evpn
"""


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def before(lines, a, b):
    return lines.index(a) < lines.index(b)


def test_add_creates_vpn_instances_before_what_binds_to_them():
    lines = patch(BASE, ROUTED).splitlines()
    assert before(lines, "ip vpn-instance web-500", "interface Vbdif500")
    assert before(lines, "ip vpn-instance FW", "interface Vlanif3000")
    assert before(lines, "vlan batch 3000", "interface Vlanif3000")
    assert before(lines, " ip binding vpn-instance web-500", " ip address 10.100.0.1 255.255.255.0")
    assert before(lines, "ip vpn-instance FW", "ip route-static vpn-instance FW 10.100.0.0 255.255.255.0 vpn-instance web-500")
    assert before(lines, "ip vpn-instance web-500", " ipv4-family vpn-instance web-500")


def test_vlan_batch_adds_only_the_service_vlan():
    lines = patch(BASE, ROUTED).splitlines()
    assert "vlan batch 3000" in lines
    assert not any("4093" in line for line in lines)


def test_remove_undoes_in_reverse_order():
    lines = patch(ROUTED, BASE).splitlines()
    assert before(lines, "undo ip route-static vpn-instance web-500 0.0.0.0 0.0.0.0 vpn-instance FW 10.255.0.2",
                  "undo ip vpn-instance FW")
    assert before(lines, "undo interface Vlanif3000", "undo ip vpn-instance FW")
    assert before(lines, "undo interface Vlanif3000", "undo vlan batch 3000")
    # A segment's instance goes first, after the routes that use it; VRP removes its
    # BGP-VPN blocks and the Vbdif's binding with it, so they are not undone separately.
    for route in ("undo ip route-static vpn-instance web-500 0.0.0.0 0.0.0.0 vpn-instance FW 10.255.0.2",
                  "undo ip route-static vpn-instance FW 10.100.0.0 255.255.255.0 vpn-instance web-500"):
        assert before(lines, route, "undo ip vpn-instance web-500")
    assert before(lines, "undo ip vpn-instance web-500", "undo interface Vbdif500")
    assert not any("vpn-instance web-500" in line and "ipv4-family" in line for line in lines)
    assert " undo vpn-instance web-500" not in lines
    assert "  undo vpn-target 65001:1010500 export-extcommunity" in lines
    assert "undo bridge-domain 500" not in lines


def test_unchanged_tenant_needs_no_patch():
    assert patch(ROUTED, ROUTED) == ""


def test_vlan_batch_lines_from_two_generators_match_the_device_merged_line():
    # MlagBypass and FirewallServices each yield their own "vlan batch"; the device shows one.
    assert patch("vlan batch 3000 4093\n", "vlan batch 4093\nvlan batch 3000\n") == ""
    assert patch("vlan batch 3000 4093\n", "vlan batch 4093\n").splitlines() == ["undo vlan batch 3000"]


def test_bgp_vpn_instance_is_created_before_its_family_and_removed_after():
    # V300 rejects the family before its BGP-VPN instance ("vrf-session not exist").
    family_first = BASE + " ipv4-family vpn-instance web-500\n  import-route direct\n vpn-instance web-500\n"
    lines = patch(BASE, family_first).splitlines()
    assert before(lines, " vpn-instance web-500", " ipv4-family vpn-instance web-500")

    # Removed from BGP while the instance stays: family first, then the BGP-VPN instance.
    bgp_gone = ROUTED.split("bgp 65001\n")[0] + "bgp 65001\n peer 10.10.0.1 as-number 65001\n"
    lines = patch(ROUTED, bgp_gone).splitlines()
    assert before(lines, " undo ipv4-family vpn-instance web-500", " undo vpn-instance web-500")
    assert "undo ip vpn-instance web-500" not in lines


def test_a_renamed_segment_replaces_its_vpn_instance_old_one_first():
    """the new instance takes the old one's RD and L3 VNI, which V300 refuses while the old
    one exists; removing it also removes its BGP-VPN blocks and the Vbdif's binding (leaf1
    no-commit CLI tests), so the patch is the sequence V300 accepted there."""
    lines = patch(ROUTED, ROUTED.replace("web-500", "frontend-500")).splitlines()
    for route in ("undo ip route-static vpn-instance web-500 0.0.0.0 0.0.0.0 vpn-instance FW 10.255.0.2",
                  "undo ip route-static vpn-instance FW 10.100.0.0 255.255.255.0 vpn-instance web-500"):
        assert before(lines, route, "undo ip vpn-instance web-500")
    assert before(lines, "undo ip vpn-instance web-500", "ip vpn-instance frontend-500")
    assert before(lines, "ip vpn-instance frontend-500", "interface Vbdif500")
    assert before(lines, " ip binding vpn-instance frontend-500", " ip address 10.100.0.1 255.255.255.0")
    assert before(lines, " vpn-instance frontend-500", " ipv4-family vpn-instance frontend-500")
    assert not any("undo ip binding" in line or "undo vpn-instance" in line or "undo ipv4-family" in line
                   for line in lines), lines
    assert lines.count("bgp 65001") == 1


FW_BGP = """\
 vpn-instance FW
  peer 10.255.0.2 as-number 65100
  peer 10.255.0.2 bfd enable
 ipv4-family vpn-instance FW
  import-route static
  peer 10.255.0.2 enable
"""


def test_firewall_peer_is_declared_before_it_is_enabled_and_removed_in_reverse():
    with_fw = ROUTED + FW_BGP
    lines = patch(ROUTED, with_fw).splitlines()
    assert before(lines, "  peer 10.255.0.2 as-number 65100", "  peer 10.255.0.2 bfd enable")
    assert before(lines, " vpn-instance FW", " ipv4-family vpn-instance FW")
    assert before(lines, "  peer 10.255.0.2 bfd enable", "  peer 10.255.0.2 enable")

    lines = patch(with_fw, ROUTED).splitlines()
    assert before(lines, " undo ipv4-family vpn-instance FW", " undo vpn-instance FW")


def bgp_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip() not in ("quit", "")]


def test_a_firewall_asn_change_recreates_the_peer_with_all_its_lines():
    # Deploy #266 (example) was refused at `undo peer 10.150.25.5 as-number 4200000102`:
    # V300 removes a peer only with `undo peer <ip>`, which wipes its BFD line
    # and its `peer <ip> enable` in the family too, so both are sent again.
    old = ROUTED + FW_BGP
    new = ROUTED + FW_BGP.replace("as-number 65100", "as-number 65101")
    lines = bgp_lines(patch(old, new))
    assert lines == [
        "bgp 65001",
        " vpn-instance FW",
        "  undo peer 10.255.0.2",
        "  peer 10.255.0.2 as-number 65101",
        "  peer 10.255.0.2 bfd enable",
        " ipv4-family vpn-instance FW",
        "  peer 10.255.0.2 enable",
    ], "\n".join(lines)


def test_removing_one_peer_of_a_bgp_vpn_instance_is_undo_peer_alone():
    second = FW_BGP.replace("  peer 10.255.0.2 bfd enable\n",
                            "  peer 10.255.0.2 bfd enable\n  peer 10.255.0.6 as-number 65100\n") \
        + "  peer 10.255.0.6 enable\n"
    lines = bgp_lines(patch(ROUTED + second, ROUTED + FW_BGP))
    assert lines == ["bgp 65001", " vpn-instance FW", "  undo peer 10.255.0.6"], "\n".join(lines)


def test_unchanged_bgp_vpn_peers_need_no_patch():
    assert patch(ROUTED + FW_BGP, ROUTED + FW_BGP) == ""


def test_a_segment_removal_leaves_no_empty_bgp_block():
    """On a switch: deleting the segment's BGP-VPN blocks (left out, VRP removes them with the
    instance) left a bare `bgp 65001` in the patch."""
    lines = patch(ROUTED, BASE).splitlines()
    assert "bgp 65001" not in lines, lines

