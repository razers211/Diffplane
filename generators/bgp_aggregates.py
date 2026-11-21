import ipaddress
from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.bgp_evpn import fabric_asn
from generators.bgp_peers import SEG, family
from generators.fabric import SERVICE_VPN, check_vpn, device_routing, is_segment_vpn, owned, v6


def _words(aggregate: dict) -> list:
    net = ipaddress.ip_network(aggregate["prefix"])
    if net.version == 6:   # `aggregate <net> <len>`
        return ["aggregate", v6(net.network_address), str(net.prefixlen)]
    return ["aggregate", str(net.network_address), str(net.netmask)]


class BgpAggregates(PartialGenerator):
    """BGP aggregates (summaries) from the routing intent, in the global IPv4 family or a
    platform VPN instance's family; detail-suppressed or with a suppress-policy.

    Owns exactly the aggregates in intent or retired, by (VPN instance, prefix). Verified on
    a test switch: `aggregate <net> <mask> detail-suppressed` and `... suppress-policy <n>`.
    """

    TAGS = ["routing", "bgp"]

    def acl_huawei(self, device: Any) -> str:
        aggregates = owned(device, "aggregates")
        if not aggregates:
            return ""

        def rows(selected):
            lines = []
            for a in selected:
                line = " ".join(_words(a))
                lines += [f"        {line}", f"        {line} ~"]
            return lines

        result = ["bgp */\\d+/ %cant_delete=1"]
        for af in ("ipv4-family", "ipv6-family"):
            mine = [a for a in aggregates if family(a, "prefix") == af]
            global_ = [a for a in mine if not a.get("vpn")]
            seg = [a for a in mine if is_segment_vpn(a.get("vpn"))]
            service = [a for a in mine if a.get("vpn") and not is_segment_vpn(a["vpn"])]
            if global_:
                result += [f"    {af} unicast %cant_delete=1", *rows(global_)]
            if seg:
                result += [f"    {af} vpn-instance {SEG} %cant_delete=1", *rows(seg)]
            if service:
                result += [f"    {af} vpn-instance {SERVICE_VPN} %cant_delete=1", *rows(service)]
        return "\n".join(result)

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        aggregates = device_routing(device).get("aggregates") or []
        if not aggregates:
            return
        for aggregate in aggregates:
            check_vpn(device, aggregate.get("vpn"), f"aggregate {aggregate['prefix']}")
        with self.block("bgp", fabric_asn(device)):
            for af, vpn in sorted({(family(a, "prefix"), a.get("vpn") or "") for a in aggregates}):
                with self.block(*((af, "vpn-instance", vpn) if vpn else (af, "unicast"))):
                    for aggregate in (a for a in aggregates if (a.get("vpn") or "") == vpn and family(a, "prefix") == af):
                        words = _words(aggregate)
                        if aggregate.get("detail_suppressed"):
                            words.append("detail-suppressed")
                        elif aggregate.get("suppress_policy"):
                            words += ["suppress-policy", aggregate["suppress_policy"]]
                        yield tuple(words)
