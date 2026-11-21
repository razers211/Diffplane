from types import SimpleNamespace

from generators import netstream


def test_imported_samples_its_vlan_trunks_and_skips_port_less_services_and_trunk_uplinks(monkeypatch):
    """An imported fabric: services with no port of their own (service_port null) crashed the
    generator; their VLANs ride the kept VLAN trunks, which are sampled instead."""
    service = SimpleNamespace(port=None, on=lambda name: True)
    monkeypatch.setattr(netstream, "port_segments", lambda d: [SimpleNamespace(port="10GE1/0/3")])
    monkeypatch.setattr(netstream, "services", lambda d: {"FW-DATA": service})
    monkeypatch.setattr(netstream, "device_value", lambda d, k, default: {"Eth-Trunk1": {"vlans": "2 to 4094"}})
    monkeypatch.setattr(netstream, "is_leaf", lambda d: True)
    monkeypatch.setattr(netstream, "fabric_links", lambda d: [SimpleNamespace(interface="Eth-Trunk101"),
                                                               SimpleNamespace(interface="100GE1/0/1")])
    device = SimpleNamespace(name="rack1-leaf1")
    assert netstream.edge_ports(device) == ["10GE1/0/3", "Eth-Trunk1"]
    assert netstream.uplinks(device) == ["100GE1/0/1"]
