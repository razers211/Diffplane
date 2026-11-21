from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import (segment_vpn_acl, firewalled_here, has_overlay, in_segment, is_segment_vpn, local_services,
                               retired_firewalls, scheme_vpns, v6)


class FirewallServices(PartialGenerator):
    """Inter-VPN static routes of firewall services: the segment subnet into
    the segment's instance, and the segment's default route to the firewall next hop.

    The service's Vlanif and port are the Vlanifs generator's, its VLAN the Vlans
    generator's. Never touches the management VPN instance's routes. Format verified on V300;
    verified on the lab switch (V300R023).
    """

    TAGS = ["tenants", "firewall"]

    def acl_huawei(self, device: Any) -> str:
        seg = segment_vpn_acl(device)
        if not has_overlay(device):
            return ""
        # Static routes are routing objects (split by object): only the two
        # inter-VPN shapes generated here, for this leaf's firewall services and for services
        # deleted from NetBox and not yet removed from it (retired). Never _management_vpn_.
        # A firewall in a segment's own VPN instance gives that segment a plain
        # default route instead: owned exactly, by its next hops, so hand-made routes there stay.
        firewalls = [s for s in local_services(device) if s.kind == "firewall"]
        own = [(s.vrf, s.next_hop, *([s.ipv6.next_hop] if s.ipv6 else [])) for s in firewalls if in_segment(device, s)]
        segment_names = set(scheme_vpns(device))
        retired = retired_firewalls(device)
        own += [r for r in retired if len(r) > 1 and (is_segment_vpn(r[0]) or r[0] in segment_names)]
        names = sorted({s.vrf for s in firewalls if not in_segment(device, s)}
                       | {r[0] for r in retired if not (is_segment_vpn(r[0]) or r[0] in segment_names)})
        routes = [line for name in names for line in (
            f"ip route-static vpn-instance {name} * * vpn-instance {seg}",
            f"ip route-static vpn-instance {seg} 0.0.0.0 0.0.0.0 vpn-instance {name} *",
            # the same in IPv6 (V300 displays the default route as `:: 0`).
            f"ipv6 route-static vpn-instance {name} * * vpn-instance {seg}",
            f"ipv6 route-static vpn-instance {seg} :: 0 vpn-instance {name} *",
        )]
        for vrf, *hops in sorted(set(own)):
            for hop in hops:
                routes.append(f"ipv6 route-static vpn-instance {vrf} :: 0 {v6(hop)}" if ":" in hop
                              else f"ip route-static vpn-instance {vrf} 0.0.0.0 0.0.0.0 {hop}")
        return "\n".join(routes)

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not has_overlay(device):
            return
        services = {s.name: s for s in local_services(device)}
        for segment in firewalled_here(device):
            service = services[segment.firewall]
            if service.vrf == segment.vrf:   # the firewall sits in the segment's own instance: its default only
                yield "ip route-static vpn-instance", segment.vrf, "0.0.0.0 0.0.0.0", service.next_hop
                if segment.ipv6 is not None and service.ipv6 is not None:
                    yield "ipv6 route-static vpn-instance", segment.vrf, ":: 0", v6(service.ipv6.next_hop)
                continue
            vrf = service.vrf
            network = segment.gateway.network
            yield ("ip route-static vpn-instance", vrf, str(network.network_address), str(network.netmask),
                   "vpn-instance", segment.vrf)
            # Dotted mask: V300 displays "0.0.0.0 0.0.0.0" whichever form was entered (a test switch).
            yield ("ip route-static vpn-instance", segment.vrf, "0.0.0.0 0.0.0.0 vpn-instance", vrf,
                   service.next_hop)
            # a DHCP server behind the firewall answers each gateway leaf's relay loopback.
            if segment.relay and any(r.service == service.name for r in segment.relay.servers):
                for _, address in segment.relay.loopbacks:
                    yield ("ip route-static vpn-instance", vrf, address.split("/")[0], "255.255.255.255",
                           "vpn-instance", segment.vrf)
            if segment.ipv6 is None or service.ipv6 is None:
                continue   # IPv6 through the firewall needs both dual-stack
            six = segment.ipv6.gateway.network
            yield ("ipv6 route-static vpn-instance", vrf, v6(six.network_address), str(six.prefixlen),
                   "vpn-instance", segment.vrf)
            yield "ipv6 route-static vpn-instance", segment.vrf, ":: 0 vpn-instance", vrf, service.ipv6.next_hop
            if segment.relay and any(r.service == service.name for r in segment.ipv6.servers):
                for _, address in segment.relay.loopbacks6:
                    yield ("ipv6 route-static vpn-instance", vrf, v6(address.split("/")[0]), "128",
                           "vpn-instance", segment.vrf)
