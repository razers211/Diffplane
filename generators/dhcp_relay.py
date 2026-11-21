from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import gateway_segments, has_overlay, ip_with_mask, pip_layout, v6_interface

# LoopBack2 to LoopBack1023: LoopBack0 and 1 are the Loopbacks generator's (own address, VTEP),
# and LoopBack2 too in the PIP layout (generators/fabric.py pip_layout).
RELAY_LOOPBACK = "*/LoopBack([2-9]|[1-9]\\d|[1-9]\\d\\d|10[01]\\d|102[0-3])$/"
RELAY_LOOPBACK_PIP = "*/LoopBack([3-9]|[1-9]\\d|[1-9]\\d\\d|10[01]\\d|102[0-3])$/"


def relaying(device: Any) -> list:
    """This switch's gateways that relay DHCP and have their relay loopback here."""
    return [s for s in gateway_segments(device)
            if s.relay and (s.relay.address(device.name) or s.relay.address6(device.name))]


class DhcpRelay(PartialGenerator):
    """DHCP relay: `dhcp enable` while a gateway here relays, and each
    relaying segment's loopback in its VPN instance with this switch's own /32 from the fabric's
    relay pool. The relay lines on the VBDIF are the Gateways generator's (it owns the VBDIF).

    Owns LoopBack2 to LoopBack1023 by kind on overlay switches, so a relay turned off removes its
    loopback; a hand-made loopback numbered there shows as a removal. Never LoopBack0 or 1.
    """

    TAGS = ["tenants", "dhcp"]

    def acl_huawei(self, device: Any) -> str:
        if not has_overlay(device):
            return ""
        relay = RELAY_LOOPBACK_PIP if pip_layout(device) else RELAY_LOOPBACK
        return "\n".join(["dhcp enable", f"interface {relay}  %cant_delete=0", "    ~ %global"])

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not has_overlay(device):
            return
        segments = relaying(device)
        if not segments:
            return
        yield "dhcp enable"
        for segment in segments:
            with self.block("interface", segment.relay.interface):
                yield "ip binding vpn-instance", segment.vrf
                if (four := segment.relay.address(device.name)) is not None:
                    yield "ip address", ip_with_mask(four)
                if (six := segment.relay.address6(device.name)) is not None:   # DHCPv6 relay
                    yield "ipv6 enable"
                    yield "ipv6 address", v6_interface(six)
