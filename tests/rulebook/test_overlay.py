"""NVE and BGP EVPN patches with our rulebook."""

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

NVE = """\
evpn-overlay enable
interface Nve1
 source 10.10.1.1
 pip-source 10.10.0.2 peer 10.10.0.3 bypass
 mac-address 0200-0a0a-0101
"""
BGP = """\
bgp 65001
 router-id 10.10.0.2
 peer 10.10.0.1 as-number 65001
 peer 10.10.0.1 connect-interface LoopBack0
 l2vpn-family evpn
  peer 10.10.0.1 enable
"""


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def test_nve_after_evpn_overlay_and_removed_before_it():
    added = patch("", NVE).splitlines()
    removed = patch(NVE, "").splitlines()

    assert added.index("evpn-overlay enable") < added.index("interface Nve1")
    assert removed == ["undo interface Nve1", "undo evpn-overlay enable"]


def test_bgp_peer_source_is_set_before_the_family_enables_it():
    lines = patch("", BGP).splitlines()

    assert lines.index(" peer 10.10.0.1 as-number 65001") < lines.index(" peer 10.10.0.1 connect-interface LoopBack0")
    assert lines.index(" peer 10.10.0.1 connect-interface LoopBack0") < lines.index("  peer 10.10.0.1 enable")


def test_new_spine_peer_is_added_without_touching_existing_ones():
    more = BGP.replace(" l2vpn-family evpn\n", " peer 10.10.0.9 as-number 65001\n peer 10.10.0.9 connect-interface LoopBack0\n l2vpn-family evpn\n") + "  peer 10.10.0.9 enable\n"

    lines = patch(BGP, more).splitlines()

    assert not any("10.10.0.1" in line for line in lines)
    assert "  peer 10.10.0.9 enable" in lines


def test_unchanged_overlay_needs_no_patch():
    assert patch(NVE + BGP, NVE + BGP) == ""


def test_advertise_irb_is_added_after_the_peer_is_enabled_and_undone_alone():
    irb = BGP + "  peer 10.10.0.1 advertise irb\n"
    added = patch(BGP, irb).splitlines()
    assert [line.strip() for line in added if "irb" in line] == ["peer 10.10.0.1 advertise irb"]
    assert [line.strip() for line in patch(irb, BGP).splitlines() if "irb" in line] == ["undo peer 10.10.0.1 advertise irb"]
    new = patch("", irb).splitlines()
    assert new.index("  peer 10.10.0.1 enable") < new.index("  peer 10.10.0.1 advertise irb")
