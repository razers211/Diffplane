from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import (centralized, gateway_segments, ip_with_mask, has_overlay, not_management, routed_segments,
                               services, v6, v6_interface)


class MissingGatewayMac(Exception):
    pass


class Gateways(PartialGenerator):
    """Anycast gateway: a VBDIF per routed segment attached on the leaf (symmetric), or per
    routed segment of the fabric on the gateway pair (centralized).

    Bound to the segment's VPN instance, same address and fabric-wide MAC on every leaf.
    Verified on the lab switch (V300R023).

    DHCP relay lives here too, because this generator owns every line under a VBDIF: the
    servers, the per-leaf relay loopback as giaddr, Option 82 link-selection and server-id-override
    (forms from console tests). Only where this switch's relay loopback
    exists in NetBox: a VBDIF never relays from the shared gateway address.
    """

    TAGS = ["tenants", "gateways"]

    def acl_huawei(self, device: Any) -> str:
        if not has_overlay(device):
            return ""
        return f"interface */Vbdif{not_management(device, 'bridge_domains')}\\d+/  %cant_delete=0\n    ~ %global"

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not has_overlay(device):
            return
        segments = gateway_segments(device)
        if not segments:
            return
        mac = (device.config_context or {}).get("fabric", {}).get("gateway_mac")
        if not mac:
            raise MissingGatewayMac(f"{device.name}: fabric.gateway_mac is not set in the fabric YAML")
        for segment in segments:
            scheme = segment.scheme   # an imported fabric's own gateway forms
            with self.block("interface", f"Vbdif{segment.bd_id}"):
                yield "ip binding vpn-instance", segment.vrf
                yield "ip address", ip_with_mask(segment.gateway)
                if scheme.get("arp_broadcast_detect"):
                    yield "arp broadcast-detect enable"
                yield "mac-address", scheme.get("gateway_mac") or mac
                if scheme.get("anycast", True):
                    yield "vxlan anycast-gateway enable"
                if not centralized(device) or scheme.get("arp_collect_host"):
                    # Host routes over EVPN serve distributed gateways only.
                    yield "arp collect host enable"
                yield from _ipv6(device, segment)
                yield from _relay(device, segment)


def _ipv6(device: Any, segment) -> Iterator[str | tuple[str, ...]]:
    """The IPv6 anycast gateway (::1 of the segment's /64) next to the IPv4 one, and how its
    hosts get addresses: router advertisements (halted by default on V300) for SLAAC and DHCPv6,
    the managed and other flags and the relay for DHCPv6."""
    six = segment.ipv6
    if six is None:
        return
    yield "ipv6 enable"
    yield "ipv6 address", v6_interface(six.gateway)
    if not centralized(device):
        yield "ipv6 nd collect host enable"
    if six.hosts == "none":
        return
    yield "ipv6 nd ra halt disable"
    if six.hosts == "dhcpv6":
        yield "ipv6 nd autoconfig managed-address-flag"
        yield "ipv6 nd autoconfig other-flag"
        relay = segment.relay
        if six.servers and relay is not None and relay.address6(device.name) is not None:
            routed = routed_segments(device)
            for server in six.servers:
                if server.service:
                    yield "dhcpv6 relay destination", v6(server.address), "vpn-instance", _vrf(device, server.service)
                elif server.segment is not None and server.segment in routed:
                    yield "dhcpv6 relay destination", v6(server.address), "vpn-instance", routed[server.segment].vrf
                else:
                    yield "dhcpv6 relay destination", v6(server.address)
            yield "dhcpv6 relay source-interface", relay.interface


def _relay(device: Any, segment) -> Iterator[str | tuple[str, ...]]:
    relay = segment.relay
    if relay is None or not relay.servers or relay.address(device.name) is None:
        return
    routed = routed_segments(device)
    yield "dhcp select relay"
    for server in relay.servers:
        if server.service:
            yield "dhcp relay server-ip", server.address, "vpn-instance", _vrf(device, server.service)
        elif server.segment is not None and server.segment in routed:
            yield "dhcp relay server-ip", server.address, "vpn-instance", routed[server.segment].vrf
        else:   # the segment's own VPN instance: the VBDIF's
            yield "dhcp relay server-ip", server.address
    yield "dhcp relay giaddr source-interface", relay.interface
    yield "dhcp option82 link-selection insert enable"
    yield "dhcp option82 server-id-override insert enable"
    yield "dhcp relay information enable"


def _vrf(device: Any, service: str) -> str:
    """The VPN instance a relay server behind a service is reached in (services share them)."""
    found = services(device).get(service)
    return found.vrf if found else service
