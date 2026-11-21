"""Patches for DHCP relay, both directions, with our rulebook: the relay loopback exists
before the VBDIF names it, and it and `dhcp enable` go after the VBDIFs stopped relaying."""

from pathlib import Path

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

GOLDEN = Path(__file__).parents[1] / "golden"
BEFORE = (GOLDEN / "example-routing" / "leaf1.cfg").read_text()   # routed segments, no relay
AFTER = (GOLDEN / "example-relay" / "leaf1.cfg").read_text()      # the same with relay on web and db


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and line.strip() != "quit"]


def test_relay_on_makes_the_loopbacks_before_the_vbdifs_name_them():
    result = lines(patch(BEFORE, AFTER))
    assert result.index("interface LoopBack2") < result.index("interface Vbdif500")
    assert result.index("interface LoopBack3") < result.index("interface Vbdif600")
    assert result.index("dhcp enable") < result.index("interface Vbdif500")
    assert "ip route-static vpn-instance FW 10.10.8.2 255.255.255.255 vpn-instance web-500" in result


def test_relay_off_stops_the_vbdifs_first_then_removes_loopbacks_and_dhcp():
    result = lines(patch(AFTER, BEFORE))
    last_vbdif = result.index("undo dhcp relay information enable", result.index("interface Vbdif600"))
    assert result.index("undo interface LoopBack2") > last_vbdif and result.index("undo interface LoopBack3") > last_vbdif
    assert result[-1] == "undo dhcp enable"
    # V300 refuses the interface's name in this undo (leaf1 deploy)
    assert "undo dhcp relay giaddr source-interface" in result
    assert not any(line.startswith("undo dhcp relay giaddr source-interface LoopBack") for line in result)
    assert "undo dhcp relay server-ip 10.200.0.5 vpn-instance FW" in result   # the full form, as the console offers
