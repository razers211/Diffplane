"""Patches for the M-LAG bypass (Vlanif over the peer-link and the route to the peer)."""

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

BYPASS = """\
vlan batch 4093
vlan 4093
 description vxlan-bypass
interface Vlanif4093
 ip address 10.12.0.1 255.255.255.252
 reserved for vxlan bypass
ip route-static 10.10.0.3 255.255.255.255 10.12.0.2 preference 1
"""


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def test_add_creates_vlan_then_vlanif_then_route():
    lines = patch("", BYPASS).splitlines()

    assert lines.index("vlan batch 4093") < lines.index("interface Vlanif4093") < lines.index(
        "ip route-static 10.10.0.3 255.255.255.255 10.12.0.2 preference 1"
    )


def test_remove_route_first_then_vlanif_then_vlan():
    assert patch(BYPASS, "").splitlines() == [
        "undo ip route-static 10.10.0.3 255.255.255.255 10.12.0.2",
        "undo interface Vlanif4093",
        "undo vlan 4093",
        "undo vlan batch 4093",
    ]


def test_peer_loopback_change_replaces_only_the_route():
    changed = BYPASS.replace("10.10.0.3 255.255.255.255", "10.10.0.9 255.255.255.255")

    assert patch(BYPASS, changed).splitlines() == [
        "undo ip route-static 10.10.0.3 255.255.255.255 10.12.0.2",
        "ip route-static 10.10.0.9 255.255.255.255 10.12.0.2 preference 1",
    ]


def test_unchanged_bypass_needs_no_patch():
    assert patch(BYPASS, BYPASS) == ""
