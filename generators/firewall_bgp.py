from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.bgp_evpn import fabric_asn
from generators.fabric import (has_overlay, in_segment, is_segment_vpn, local_services, retired_firewalls, scheme_vpns,
                               service_scheme, service_vpn)


class FirewallBgp(PartialGenerator):
    """eBGP with each firewall that has an ASN, inside its service VPN instance.

    The leaf advertises the segment subnets it stitches to the firewall (import-route
    static: the inter-VPN routes into the segments) and learns the firewall's routes.
    BFD at default timers. Format seen on a test switch (V300, `display configuration
    candidate changes`): the peer is declared in the BGP-VPN instance view, enabled in
    the address family; peers cannot be declared in the family with `bgp yang-mode enable`.
    """

    TAGS = ["tenants", "firewall", "bgp"]

    def acl_huawei(self, device: Any) -> str:
        if not has_overlay(device):
            return ""
        # BgpEvpn owns (and alone may delete) the bgp line; TenantBgp owns segment instances. Every other
        # BGP-VPN instance and family is a service's container, owned by kind (listed in NetBox
        # or not). Inside them only the firewall's own peer lines (by its next hop: they go when
        # the ASN is cleared) and import-route static; other peers are routing objects.
        # ISP services have no session here: theirs is a routing-intent peer (BgpPeers).
        segment_names = set(scheme_vpns(device))
        segment = lambda vrf: is_segment_vpn(vrf) or vrf in segment_names   # noqa: E731
        everyone = [s for s in local_services(device) if s.kind == "firewall"]
        # Services sharing an instance: a deleted firewall's session goes by its
        # next hops while the instance stays (retired until a verified deploy).
        retired = [r for r in retired_firewalls(device) if len(r) > 1]
        hops4 = [s.next_hop for s in everyone if not in_segment(device, s)]
        hops6 = [s.ipv6.next_hop for s in everyone if s.ipv6 is not None and not in_segment(device, s)]
        for r in retired:
            if not segment(r[0]):
                hops4 += [h for h in r[1:] if ":" not in h]
                hops6 += [h for h in r[1:] if ":" in h]
        vpn = service_vpn(device)
        rows = ["bgp */\\d+/ %cant_delete=1", f"    vpn-instance {vpn}"]
        rows += [f"        peer {hop} ~" for hop in hops4 + hops6]
        rows += [f"    ipv4-family vpn-instance {vpn}", "        import-route static"]
        rows += [f"        peer {hop} enable" for hop in hops4]
        # the IPv6 session and family.
        rows += [f"    ipv6-family vpn-instance {vpn}", "        import-route static"]
        rows += [f"        peer {hop} enable" for hop in hops6]
        # A firewall in a segment's instance: TenantBgp owns those blocks and `import-route static`;
        # only the firewall's own peer lines here.
        inside: dict[str, list[str]] = {}
        for s in everyone:
            if in_segment(device, s):
                inside.setdefault(s.vrf, []).extend([s.next_hop] + ([s.ipv6.next_hop] if s.ipv6 else []))
        for r in retired:
            if segment(r[0]):
                inside.setdefault(r[0], []).extend(r[1:])
        for vrf, hops in sorted(inside.items()):
            hops = sorted(set(hops))
            rows += [f"    vpn-instance {vrf} %cant_delete=1", *(f"        peer {h} ~" for h in hops)]
            rows += [f"    ipv4-family vpn-instance {vrf} %cant_delete=1",
                     *(f"        peer {h} enable" for h in hops if ":" not in h)]
            rows += [f"    ipv6-family vpn-instance {vrf} %cant_delete=1",
                     *(f"        peer {h} enable" for h in hops if ":" in h)]
        return "\n".join(rows)

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not has_overlay(device):
            return
        services = [s for s in local_services(device) if s.kind == "firewall" and s.asn is not None]
        if not services:
            return
        by_vrf: dict[str, list] = {}   # services sharing a VPN instance: one block each
        for service in services:
            by_vrf.setdefault(service.vrf, []).append(service)
        with self.block("bgp", fabric_asn(device)):
            for vrf, group in sorted(by_vrf.items()):
                with self.block("vpn-instance", vrf):
                    for service in group:
                        scheme = service_scheme(device, service.name)   # an imported fabric's iBGP to its firewall contexts
                        yield "peer", service.next_hop, "as-number", service.asn
                        if scheme.get("connect_interface"):
                            yield "peer", service.next_hop, "connect-interface", f"Vlanif{service.vlan}"
                        if scheme.get("bfd", True):
                            yield "peer", service.next_hop, "bfd enable"
                        if service.ipv6 is not None:
                            yield "peer", service.ipv6.next_hop, "as-number", service.asn
                            yield "peer", service.ipv6.next_hop, "bfd enable"
            for vrf, group in sorted(by_vrf.items()):
                with self.block("ipv4-family vpn-instance", vrf):
                    if not in_segment(device, group[0]):   # in a segment's family it is TenantBgp's
                        yield "import-route static"
                    for service in group:
                        yield "peer", service.next_hop, "enable"
            for vrf, group in sorted(by_vrf.items()):
                sixes = [s for s in group if s.ipv6 is not None]
                if sixes:
                    with self.block("ipv6-family vpn-instance", vrf):
                        if not in_segment(device, group[0]):
                            yield "import-route static"
                        for service in sixes:
                            yield "peer", service.ipv6.next_hop, "enable"
