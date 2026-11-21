"""Patches for the NetStream generator's lines, both directions, with our rulebook: the
record is created before a port uses it and removed after the ports left it."""

import pytest

from tests.rulebook.patch import patch, use_our_rulebook

PORTS = """\
interface 100GE1/0/1
 undo portswitch
interface 10GE1/0/3
"""

NETSTREAM = """\
netstream export ip source 172.24.0.11
netstream export ip host 198.51.100.2 2055 vpn-instance _management_vpn_ dscp 0
netstream export vxlan inner-ip source 172.24.0.11
netstream export vxlan inner-ip host 198.51.100.2 2055 vpn-instance _management_vpn_ dscp 0
netstream record FLOWS-VXLAN vxlan inner-ip
 match inner-ip source-address
 match inner-ip destination-address
 collect counter bytes
interface 100GE1/0/1
 undo portswitch
 netstream sampler random-packets 1024 inbound
 netstream record FLOWS-VXLAN vxlan inner-ip
interface 10GE1/0/3
 netstream sampler random-packets 1024 inbound
 netstream inbound ip
"""


@pytest.fixture(autouse=True)
def our_rulebook(monkeypatch):
    use_our_rulebook(monkeypatch)


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def test_turning_netstream_on_creates_the_record_before_the_uplink_uses_it():
    result = lines(patch(PORTS, NETSTREAM))
    assert result.index("netstream record FLOWS-VXLAN vxlan inner-ip") < result.index("interface 100GE1/0/1")
    assert "netstream export ip host 198.51.100.2 2055 vpn-instance _management_vpn_ dscp 0" in result
    assert result[result.index("interface 10GE1/0/3") + 1:][:2] == [
        "netstream sampler random-packets 1024 inbound", "netstream inbound ip"]


def test_turning_it_off_leaves_the_ports_before_the_record_goes():
    result = lines(patch(NETSTREAM, PORTS))
    assert result[-1] == "undo netstream record FLOWS-VXLAN vxlan inner-ip"
    assert result.index("undo netstream record FLOWS-VXLAN vxlan inner-ip") > result.index("interface 100GE1/0/1")
    assert "undo netstream export ip host 198.51.100.2 2055 vpn-instance _management_vpn_ dscp 0" in result
    assert "undo netstream inbound ip" in result
