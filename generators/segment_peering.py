from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import segment_vpn_acl, centralized, gateway_segments, is_gateway, has_overlay, v6


class SegmentPeering(PartialGenerator):
    """Explicit peering between segments in a centralized fabric: both VPN instances are
    on the gateway pair, so each gets the other's subnet as an inter-VPN static route (the
    firewall services' form; both directions verified on a test switch, no-commit).

    A symmetric fabric peers through L3 route targets instead (TenantVrfs). Only routes from one
    segment instance to another are owned, on the gateway pair only, so hand-made inter-VPN
    routes elsewhere are never touched.
    """

    TAGS = ["tenants", "peering"]

    def acl_huawei(self, device: Any) -> str:
        seg = segment_vpn_acl(device)
        if not has_overlay(device) or not centralized(device) or not is_gateway(device):
            return ""
        return "\n".join([f"ip route-static vpn-instance {seg} * * vpn-instance {seg}",
                          # the same in IPv6
                          f"ipv6 route-static vpn-instance {seg} * * vpn-instance {seg}"])

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not has_overlay(device) or not centralized(device):
            return
        segments = {s.vni: s for s in gateway_segments(device)}
        for segment in segments.values():
            for vni in segment.peers:
                peer = segments.get(vni)
                if peer is None:
                    continue
                network = peer.gateway.network
                yield ("ip route-static vpn-instance", segment.vrf, str(network.network_address), str(network.netmask),
                       "vpn-instance", peer.vrf)
                if segment.ipv6 is not None and peer.ipv6 is not None:
                    six = peer.ipv6.gateway.network
                    yield ("ipv6 route-static vpn-instance", segment.vrf, v6(six.network_address), str(six.prefixlen),
                           "vpn-instance", peer.vrf)
