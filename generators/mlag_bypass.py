import ipaddress
from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import MissingFabricSettings, fabric_settings, fabric_value, ip_with_mask, ipv4_regex, has_overlay, loopback, mlag_peer_name, pip_layout, pip_loopback, related


def _bypass(device: Any) -> tuple[int, ipaddress.IPv4Interface] | None:
    vlan = fabric_settings(device).mlag_bypass_vlan
    if vlan is None:
        return None
    address = loopback(device, f"Vlanif{vlan}")  # any interface with one address
    return (vlan, address) if address is not None else None


def bypass_vlan(device: Any) -> int | None:
    """The bypass VLAN if this leaf has a bypass (an M-LAG peer and a bypass address)."""
    if not has_overlay(device) or mlag_peer_name(device) is None or (bypass := _bypass(device)) is None:
        return None
    return bypass[0]


def peer_address(own: ipaddress.IPv4Interface) -> ipaddress.IPv4Address:
    """The other host of a /30."""
    (peer,) = [h for h in own.network.hosts() if h != own.ip]
    return peer


class MlagBypass(PartialGenerator):
    """VXLAN bypass between M-LAG peers, as on ref-leaf1: a reserved Vlanif over the
    peer-link with a /30 from NetBox, and a static route to the peer's LoopBack0
    (its PIP; LoopBack1 in the PIP layout) through it. If the peer-link fails the route goes and OSPF via the
    spines takes over.
    """

    TAGS = ["mlag", "bypass"]

    def acl_huawei(self, device: Any) -> str:
        if not has_overlay(device) or mlag_peer_name(device) is None or _bypass(device) is None:
            return ""
        vlan, _ = _bypass(device)
        # Only host routes to fabric loopbacks (the peer's LoopBack0, or its PIP): other static
        # routes are routing objects, never removed when hand-made. With PIPs both pools, so a
        # pair moved to the PIP layout loses its route to the peer's LoopBack0.
        settings = fabric_settings(device)
        if settings.loopback_prefix is None:
            raise MissingFabricSettings(f"{device.hostname}: the fabric config context has no loopback_prefix; "
                                        "run the seed for its fabric")
        pools = [settings.loopback_prefix] + ([settings.pip_prefix] if pip_layout(device) else [])
        return "\n".join(
            [
                f"vlan {vlan}",
                "    description ~",
                "    name ~",
                f"interface Vlanif{vlan}  %cant_delete=0",
                "    ip address ~",
                "    reserved ~",
            ] + [f"ip route-static */{ipv4_regex(pool)}/ 255.255.255.255 ~" for pool in pools]
        )

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        peer_name = mlag_peer_name(device)
        bypass = _bypass(device)
        if not has_overlay(device) or peer_name is None or bypass is None:
            return
        vlan, own = bypass
        peer_loopback = loopback(related(self.storage, [peer_name])[peer_name], pip_loopback(device))

        with self.block("vlan", vlan):
            if (name := fabric_value(device, "bypass_vlan_name", None)) is not None:
                yield "name", name   # imported fabrics
            else:
                yield "description vxlan-bypass"
        with self.block("interface", f"Vlanif{vlan}"):
            yield "ip address", ip_with_mask(own)
            yield "reserved for vxlan bypass"
        yield "ip route-static", str(peer_loopback.ip), "255.255.255.255", str(peer_address(own)), "preference 1"
