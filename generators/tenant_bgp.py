from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.bgp_evpn import fabric_asn
from generators.fabric import segment_vpn_acl, centralized, firewalled_here, has_overlay, hosting_here, local_routed, tenant_vrfs


class TenantBgp(PartialGenerator):
    """BGP for each segment VPN instance: advertise it into EVPN.

    Local gateways contribute their connected subnet (import-route direct); on the leaf
    hosting the segment's firewall, the default route via the firewall is advertised to
    the other leaves (import-route static). Format verified on V300. On V300 the family
    needs `vpn-instance <name>` in the BGP view first (verified on a test switch).
    """

    TAGS = ["tenants", "bgp"]

    def acl_huawei(self, device: Any) -> str:
        seg = segment_vpn_acl(device)
        if not has_overlay(device):
            return ""
        # BgpEvpn owns (and alone may delete) the bgp line. Split by object:
        # the segment BGP-VPN instance and family are containers, owned by kind; inside them only
        # the lines generated here. Peers and aggregates in them are routing objects.
        return "\n".join([
            "bgp */\\d+/ %cant_delete=1",
            f"    vpn-instance {seg}",
            f"    ipv4-family vpn-instance {seg}",
            "        import-route direct",
            "        import-route static",
            "        advertise l2vpn evpn",
            # the same for IPv6.
            f"    ipv6-family vpn-instance {seg}",
            "        import-route direct",
            "        import-route static",
            "        advertise l2vpn evpn",
        ])

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        # Centralized: no EVPN routes between VPN instances; the ACL stays, so the blocks
        # of a fabric switched from symmetric are removed.
        if not has_overlay(device) or centralized(device):
            return
        segments = tenant_vrfs(device)
        if not segments:
            return
        # A service's Vlanif in the instance is a connected subnet too.
        local = {s.vni for s in local_routed(device) + hosting_here(device)}
        stitched = {s.vni for s in firewalled_here(device)}
        with self.block("bgp", fabric_asn(device)):
            for segment in segments:
                # V300 with `bgp yang-mode enable`: the BGP-VPN instance must exist before its
                # address family ("vrf-session not exist" otherwise, a test switch).
                yield "vpn-instance", segment.vrf
            for segment in segments:
                with self.block("ipv4-family vpn-instance", segment.vrf):
                    if segment.vni in local:
                        yield "import-route direct"
                    if segment.vni in stitched:
                        yield "import-route static"
                    yield "advertise l2vpn evpn"
            for segment in segments:
                if segment.ipv6 is None:
                    continue
                with self.block("ipv6-family vpn-instance", segment.vrf):
                    if segment.vni in local:
                        yield "import-route direct"
                    yield "advertise l2vpn evpn"
