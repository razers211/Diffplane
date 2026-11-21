import ipaddress
from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import address, check_vpn, device_routing, owned, v6


def _words(route: dict) -> list:
    net = ipaddress.ip_network(route["prefix"])
    vpn = ["vpn-instance", route["vpn"]] if route.get("vpn") else []
    if net.version == 6:   # `ipv6 route-static [vpn-instance <v>] <net> <len> <nh>`
        return ["ipv6 route-static", *vpn, v6(net.network_address), str(net.prefixlen), address(route["next_hop"])]
    return ["ip route-static", *vpn, str(net.network_address), str(net.netmask), route["next_hop"]]


class StaticRoutes(PartialGenerator):
    """Static routes from the routing intent, global or in a platform VPN instance.

    Owns exactly the routes in intent or retired, by (VPN instance, prefix, next hop): hand-made
    routes are never removed. Verified on a test switch: NULL0 next hops, global
    and in a VPN instance; `preference` as on the M-LAG bypass route.
    """

    TAGS = ["routing", "static"]

    def acl_huawei(self, device: Any) -> str:
        rows = []
        for route in owned(device, "static_routes"):
            line = " ".join(str(w) for w in _words(route))
            rows += [line, f"{line} preference *"]
        return "\n".join(rows)

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        for route in device_routing(device).get("static_routes") or []:
            check_vpn(device, route.get("vpn"), f"static route {route['prefix']}")
            words = _words(route)
            if route.get("preference") is not None:
                words += ["preference", route["preference"]]
            yield tuple(words)
