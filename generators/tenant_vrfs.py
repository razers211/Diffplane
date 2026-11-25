from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.bgp_evpn import fabric_asn
from generators.fabric import (own_loopback, MANAGED_VPN, centralized, in_segment, service_scheme, managed_vpn, has_overlay, local_services,
                               routed_segments, tenant_vrfs)


class TenantVrfs(PartialGenerator):
    """VPN instances: one per routed segment (<name>-<bd_id>), plus the firewall
    service instances on the leaf that hosts them.

    Segment instance: RD <LoopBack0>:<bd_id>, L3 VNI = L2 VNI + 1,000,000, L3 route target
    <ASN>:<L3 VNI> imported and exported for EVPN, plus the L3 route targets of explicitly
    peered segments imported; in a centralized fabric the RD only, on the gateway pair.
    Service instance: RD <LoopBack0>:<VLAN>, plain routing, no
    L3 VNI. Verified on the lab switch (V300R023).
    """

    TAGS = ["tenants", "vrfs"]

    def acl_huawei(self, device: Any) -> str:
        if not has_overlay(device):
            return ""
        # Every VPN instance but the system ones: a deleted service's instance is removed too.
        return f"ip vpn-instance {managed_vpn(device, MANAGED_VPN)}\n    ~ %global"

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not has_overlay(device):
            return
        segments, services = tenant_vrfs(device), local_services(device)
        if not segments and not services:
            return
        router_id = own_loopback(device).ip
        asn = fabric_asn(device)
        routed = routed_segments(device)
        for segment in segments:
            l3 = segment.has_l3(device)   # centralized: all routing on the gateway pair, no L3 VNI
            rd = segment.scheme.get("rd") or f"{router_id}:{segment.bd_id}"
            targets = [segment.l3_rt(asn)]
            # centralized peering is static routes (SegmentPeering), not route targets
            imports = [] if centralized(device) else [routed[p].l3_rt(asn) for p in segment.peers if p in routed]
            with self.block("ip vpn-instance", segment.vrf):
                with self.block("ipv4-family"):
                    yield "route-distinguisher", rd
                    if l3:
                        yield "vpn-target", targets[0], "export-extcommunity evpn"
                        yield "vpn-target", targets[0], "import-extcommunity evpn"
                        for rt in imports:
                            yield "vpn-target", rt, "import-extcommunity evpn"
                if segment.ipv6 is not None:
                    # its own RD and the same L3 route targets; the instance's L3 VNI serves both
                    # families (`ipv6-family` offers no vxlan).
                    with self.block("ipv6-family"):
                        yield "route-distinguisher", rd
                        if l3:
                            yield "vpn-target", targets[0], "export-extcommunity evpn"
                            yield "vpn-target", targets[0], "import-extcommunity evpn"
                            for rt in imports:
                                yield "vpn-target", rt, "import-extcommunity evpn"
                if l3:
                    yield "vxlan vni", segment.l3_vni
        # One instance per VPN name: services may share one, which keeps the RD of
        # the service that made it; a service in a segment's instance has none of its own.
        instances: dict[str, tuple[int, bool]] = {}
        for service in services:
            if in_segment(device, service):
                continue
            rd_vlan, six = instances.get(service.vrf, (service.rd_vlan or service.vlan, False))
            instances[service.vrf] = (rd_vlan, six or service.ipv6 is not None)
        for vrf, (rd_vlan, six) in sorted(instances.items()):
            rd = service_scheme(device, vrf).get("rd") or f"{router_id}:{rd_vlan}"
            with self.block("ip vpn-instance", vrf):
                with self.block("ipv4-family"):
                    yield "route-distinguisher", rd
                if six:   # as a segment's
                    with self.block("ipv6-family"):
                        yield "route-distinguisher", rd
