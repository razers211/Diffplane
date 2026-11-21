from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import FABRIC_ROLES, centralized, has_vtep, MissingFabricSettings, device_value, fabric_links, fabric_value, fabric_settings, ipv4_regex, is_leaf, loopback, related


def fabric_asn(device: Any) -> int:
    """The fabric ASN, from the fabric config context (see seed/loader.py:_with_asn)."""
    asn = fabric_settings(device).asn
    if asn is None:
        raise ValueError(f"{device.name}: the fabric config context has no ASN; run the seed")
    return asn


def evpn_peers(storage: Any, device: Any) -> list[tuple[str, str]]:
    """(name, LoopBack0) of the fabric devices across the device's fabric links: its EVPN peers.
    Imported fabrics: the peers its switches have (every leaf with both route reflectors)."""
    names = sorted(device_value(device, "evpn_peers", None) or {link.peer_device for link in fabric_links(device)})
    devices = related(storage, names)
    peers = []
    for name in names:
        peer = devices.get(name)
        if peer is None or peer.role.slug not in FABRIC_ROLES:
            continue
        address = loopback(peer, "LoopBack0")
        if address is not None:
            peers.append((name, str(address.ip)))
    return peers


class BgpEvpn(PartialGenerator):
    """iBGP EVPN overlay in the fabric ASN; spines are route reflectors for their leaves.

    Peers are the devices across the fabric links, addressed by their LoopBack0. Only
    the lines below are owned, so defaults VRP shows elsewhere in `bgp` are left alone.
    Leaf side verified on the lab switch (V300R023); the spine side is not (planned only).
    """

    TAGS = ["overlay", "bgp"]

    def _peers(self, device: Any) -> list[tuple[str, str]]:
        return evpn_peers(self.storage, device)

    def acl_huawei(self, device: Any) -> str:
        if device.role.slug not in FABRIC_ROLES:
            return ""
        # Fabric peers only: their addresses are in the LoopBack0 pool. Every other peer is a
        # routing object (hand-made ones are never removed).
        pool = fabric_settings(device).loopback_prefix
        if pool is None:
            raise MissingFabricSettings(f"{device.hostname}: the fabric config context has no loopback_prefix; "
                                        "run the seed for its fabric")
        peer = f"peer */{ipv4_regex(pool)}/"
        return "\n".join(
            # A VTEP's `evpn-overlay enable` is Nve's; everywhere else (spines) it is ours.
            ([] if has_vtep(device) else ["evpn-overlay enable"]) +
            [
                # Only the BGP process (numeric AS); never e.g. "bgp yang-mode enable".
                "bgp */\\d+/",
                "    router-id ~",
                f"    {peer} as-number ~",
                f"    {peer} connect-interface ~",
                "    l2vpn-family evpn",
                "        policy vpn-target",
                "        undo policy vpn-target",
                f"        {peer} enable",
                f"        {peer} reflect-client",
                f"        {peer} advertise irb",
            ]
        )

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        router_id = loopback(device, "LoopBack0")
        peers = self._peers(device)
        if router_id is None or not peers:
            return
        spine = not is_leaf(device) and fabric_value(device, "reflect_clients", True)   # imported fabrics: none
        if not has_vtep(device):   # V300 refuses `l2vpn-family evpn` without it (a CE6866 on V300R025C10, 2026-11-19)
            yield "evpn-overlay enable"
        with self.block("bgp", fabric_asn(device)):
            yield "router-id", str(router_id.ip)
            for _, address in peers:
                yield "peer", address, "as-number", fabric_asn(device)
                yield "peer", address, "connect-interface LoopBack0"
            with self.block("l2vpn-family evpn"):
                # Design rule: always, on every device. Route reflectors must
                # keep EVPN routes whatever their route targets, and leaves need it too.
                yield "undo policy vpn-target"
                for _, address in peers:
                    yield "peer", address, "enable"
                    if spine:
                        yield "peer", address, "reflect-client"
                    if not centralized(device):
                        # Host routes (MAC/IP with the L3 VNI) from `arp collect host enable`: without them a
                        # segment peered with another on the same leaf was unreachable (eNSP, 2026-11-20).
                        yield "peer", address, "advertise irb"
