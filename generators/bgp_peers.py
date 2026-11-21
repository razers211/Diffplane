from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.bgp_evpn import fabric_asn
from generators.fabric import SEGMENT_VPN, SERVICE_VPN, address, check_vpn, device_routing, is_segment_vpn, owned

# Parent rows written exactly as their owners write them (BgpEvpn, TenantBgp, FirewallBgp):
# Annet merges identical rows, so the containers stay deletable by their owner. A more
# specific row here would win the match and make them undeletable.
SEG = SEGMENT_VPN


def is_v6(peer: dict) -> bool:
    return ":" in str(peer["address"])


def _family_lines(peer: dict) -> list[tuple]:
    peer_address = address(peer["address"])
    prefix = "ipv6-prefix" if is_v6(peer) else "ip-prefix"
    lines = [("peer", peer_address, "enable")]
    for key, word, direction in (("import_policy", "route-policy", "import"), ("export_policy", "route-policy", "export"),
                                 ("import_prefix_list", prefix, "import"),
                                 ("export_prefix_list", prefix, "export")):
        if peer.get(key):
            lines.append(("peer", peer_address, word, peer[key], direction))
    return lines


def family(peer_or_aggregate: dict, key: str = "address") -> str:
    """The BGP address family word: ipv4-family or ipv6-family."""
    return "ipv6-family" if ":" in str(peer_or_aggregate[key]) else "ipv4-family"


class BgpPeers(PartialGenerator):
    """BGP peers from the routing intent: in the global table or a platform VPN instance,
    with route-policies and prefix lists per direction.

    Owns exactly the peers in intent or retired, by address: hand-made peers
    are never removed (auto-import adopts them first). Verified on a test switch: peer declared in
    the BGP view or BGP-VPN instance view, enabled and filtered in the address family.
    """

    TAGS = ["routing", "bgp"]

    def acl_huawei(self, device: Any) -> str:
        peers = owned(device, "bgp_peers")
        if not peers:
            return ""
        rows = ["bgp */\\d+/ %cant_delete=1"]
        global_ = sorted({address(p["address"]) for p in peers if not p.get("vpn")})
        rows += [f"    peer {a} ~" for a in global_]
        for af in ("ipv4-family", "ipv6-family"):
            here = sorted({address(p["address"]) for p in peers if not p.get("vpn") and family(p) == af})
            if here:
                rows += [f"    {af} unicast %cant_delete=1", *(f"        peer {a} ~" for a in here)]
        for parent, selected in ((f"vpn-instance {SEG}", [p for p in peers if is_segment_vpn(p.get("vpn"))]),
                                 (f"vpn-instance {SERVICE_VPN}",
                                  [p for p in peers if p.get("vpn") and not is_segment_vpn(p["vpn"])])):
            if not selected:
                continue
            rows += [f"    {parent} %cant_delete=1", *(f"        peer {a} ~" for a in sorted({address(p["address"]) for p in selected}))]
            for af in ("ipv4-family", "ipv6-family"):
                here = sorted({address(p["address"]) for p in selected if family(p) == af})
                if here:
                    rows += [f"    {af} {parent} %cant_delete=1", *(f"        peer {a} ~" for a in here)]
        return "\n".join(rows)

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        peers = device_routing(device).get("bgp_peers") or []
        if not peers:
            return
        for peer in peers:
            check_vpn(device, peer.get("vpn"), f"BGP peer {peer['address']}")
        global_ = [p for p in peers if not p.get("vpn")]
        vpns = sorted({p["vpn"] for p in peers if p.get("vpn")})
        with self.block("bgp", fabric_asn(device)):
            for peer in global_:
                yield "peer", address(peer["address"]), "as-number", peer["asn"]
            for af in ("ipv4-family", "ipv6-family"):
                if here := [p for p in global_ if family(p) == af]:
                    with self.block(af, "unicast"):
                        for peer in here:
                            yield from _family_lines(peer)
            for vpn in vpns:
                with self.block("vpn-instance", vpn):
                    for peer in (p for p in peers if p.get("vpn") == vpn):
                        yield "peer", address(peer["address"]), "as-number", peer["asn"]
            for af in ("ipv4-family", "ipv6-family"):
                for vpn in vpns:
                    if here := [p for p in peers if p.get("vpn") == vpn and family(p) == af]:
                        with self.block(af, "vpn-instance", vpn):
                            for peer in here:
                                yield from _family_lines(peer)
