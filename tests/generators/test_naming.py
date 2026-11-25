from types import SimpleNamespace

import pytest

from generators import naming


def test_names_are_built_in_each_vendors_form():
    assert naming.loopback("huawei", 0) == "LoopBack0"
    assert naming.loopback("arista", 1) == "Loopback1"
    assert naming.trunk("huawei", 3) == "Eth-Trunk3"
    assert naming.trunk("arista", 3) == "Port-Channel3"
    with pytest.raises(ValueError):
        naming.trunk("juniper", 1)


def test_names_are_read_whatever_the_vendor():
    assert naming.trunk_number("Eth-Trunk12") == 12
    assert naming.trunk_number("Port-Channel12") == 12
    assert naming.trunk_number("Eth-Trunk12.5") is None
    assert naming.trunk_number("Ethernet12") is None
    assert all(map(naming.is_management, ["MEth0/0/0", "Management1", "Management1/1"]))
    assert not naming.is_management("Ethernet1")
    assert all(map(naming.is_front_panel, ["10GE1/0/4", "100GE1/0/1", "Ethernet1", "Ethernet49/1"]))
    assert not any(map(naming.is_front_panel, ["10GE1/0/4.1", "MEth0/0/0", "Management1", "Eth-Trunk1",
                                               "Ethernet1.10", "Loopback0"]))


def test_the_vendor_comes_from_annets_hardware_view():
    assert naming.vendor_of(SimpleNamespace(hw=SimpleNamespace(vendor="arista"))) == "arista"
    assert naming.vendor_of(SimpleNamespace(hw=SimpleNamespace(vendor="huawei"))) == "huawei"
    assert naming.vendor_of(SimpleNamespace()) == "huawei"


def test_routed_vlan_and_virtual_interfaces():
    assert naming.svi("huawei", 4093) == "Vlanif4093" and naming.svi("arista", 4093) == "Vlan4093"
    assert naming.svi_vlan("Vlanif4093") == 4093 and naming.svi_vlan("Vlan4093") == 4093
    assert naming.svi_vlan("Vbdif500") is None and naming.svi_vlan("Vlanif4093.1") is None
    assert all(map(naming.is_virtual, ["LoopBack0", "Loopback1", "Vlanif4093", "Vlan10", "Vbdif500", "Nve1", "Vxlan1"]))
    assert not any(map(naming.is_virtual, ["Ethernet1", "10GE1/0/1", "Eth-Trunk1", "Management1"]))
