from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import (FRONT_PANEL_PORT, _interface, device_value, fabric_links, is_leaf, port_segments,
                               services)

RECORD = "FLOWS-VXLAN"
RATE = 1024
MANAGEMENT_VPN = "_management_vpn_"
MATCH = ("source-address", "destination-address", "protocol", "source-port", "destination-port")
COLLECT = ("counter bytes", "counter packets", "interface input")


def netstream(device: Any) -> dict | None:
    """The fabric's NetStream setting (Fabric page): {"host", "port"} while it is on."""
    setting = ((device.config_context or {}).get("fabric") or {}).get("netstream") or {}
    return setting if setting.get("enabled") else None


def management_address(device: Any) -> str | None:
    meth = _interface(device, "MEth0/0/0")
    if meth is None or not meth.ip_addresses:
        return None
    return meth.ip_addresses[0].address.split("/")[0]


def edge_ports(device: Any) -> list[str]:
    """Where traffic enters the fabric: ports and trunks carrying segments, service ports, and the VLAN
    trunks an import kept (imported fabrics: its services have no port of their own, service_port null)."""
    ports = {s.port for s in port_segments(device)}
    ports |= {s.port for s in services(device).values() if s.on(device.name)}
    ports |= set(device_value(device, "vlan_trunks", {}) or {})
    return sorted(p for p in ports if p is not None and not p.startswith("MEth"))


def uplinks(device: Any) -> list[str]:
    """A leaf's fabric links: overlay traffic arriving from other VTEPs, seen as VXLAN inner flows."""
    # shortcut: a record on an Eth-Trunk link (an imported switch) is unverified on V300, so such links are
    # not sampled; try it on the console before adding them.
    return [link.interface for link in fabric_links(device)
            if not link.interface.startswith("Eth-Trunk")] if is_leaf(device) else []


class NetStream(PartialGenerator):
    """Sampled NetStream v9, per fabric from the Fabric page.

    Exported over MEth in `_management_vpn_` (only named as the path, never changed), source the
    switch's MEth address, DSCP 0 (mandatory on V300). Edge ports (segments, services) sample
    IPv4 flows 1:1024 inbound; a leaf's fabric links sample VXLAN inner flows 1:1024 through the
    flexible record FLOWS-VXLAN (V300 refuses a record without match keys on a port). Never MEth.
    Forms from no-commit console tests.

    Owns every `netstream` line, so turning NetStream off, or a port leaving the edge, removes
    them; hand-made NetStream configuration shows as a removal.
    """

    TAGS = ["netstream"]

    def acl_huawei(self, device: Any) -> str:
        return "\n".join([
            "netstream export ~",
            "netstream record ~ %cant_delete=0",
            "    ~ %global",
            f"interface {FRONT_PANEL_PORT} %cant_delete=1",
            "    netstream ~",
            "interface */Eth-Trunk\\d+/ %cant_delete=1",
            "    netstream ~",
        ])

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        setting, source = netstream(device), management_address(device)
        if setting is None or source is None:
            return
        target = (setting["host"], setting["port"], "vpn-instance", MANAGEMENT_VPN, "dscp 0")
        yield "netstream export ip source", source
        yield ("netstream export ip host", *target)
        links = uplinks(device)
        if links:
            yield "netstream export vxlan inner-ip source", source
            yield ("netstream export vxlan inner-ip host", *target)
            with self.block("netstream record", RECORD, "vxlan inner-ip"):
                for field in MATCH:
                    yield "match inner-ip", field
                for item in COLLECT:
                    yield "collect", item
        for port in edge_ports(device):
            with self.block("interface", port):
                yield "netstream sampler random-packets", RATE, "inbound"
                yield "netstream inbound ip"
        for port in links:
            with self.block("interface", port):
                yield "netstream sampler random-packets", RATE, "inbound"
                yield "netstream record", RECORD, "vxlan inner-ip"
