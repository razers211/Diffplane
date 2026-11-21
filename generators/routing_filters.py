import ipaddress
from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import device_routing, owned, v6


class RoutingFilters(PartialGenerator):
    """Prefix lists, AS-path filters and basic community filters from the routing intent.

    Owns only the names in intent or retired: hand-made filters are never
    removed. Forms verified on a test switch (V300).
    """

    TAGS = ["routing", "filters"]

    def acl_huawei(self, device: Any) -> str:
        rows = [f"ip ip-prefix {n} ~" for n in owned(device, "prefix_lists")]
        rows += [f"ip ipv6-prefix {n} ~" for n in owned(device, "prefix_lists")]   # phase C
        rows += [f"ip as-path-filter {n} ~" for n in owned(device, "as_path_filters")]
        rows += [f"ip community-filter basic {n} ~" for n in owned(device, "community_filters")]
        return "\n".join(rows)

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        routing = device_routing(device)
        for name, rules in sorted((routing.get("prefix_lists") or {}).items()):
            for rule in sorted(rules, key=lambda r: r["index"]):
                net = ipaddress.ip_network(rule["prefix"])
                if net.version == 6:   # `ip ipv6-prefix <n> ... <net> <len>`
                    line = ["ip ipv6-prefix", name, "index", rule["index"], rule["action"], v6(net.network_address),
                            net.prefixlen]
                else:
                    line = ["ip ip-prefix", name, "index", rule["index"], rule["action"], str(net.network_address),
                            net.prefixlen]
                ge, le = rule.get("ge"), rule.get("le")
                if le is not None and ge is None:
                    # V300 stores `less-equal` alone as `greater-equal <prefix length> less-equal`
                    # (a test switch): write it as VRP displays it. Assumed the same for IPv6
                    # (unverified: its stored form was not shown).
                    ge = net.prefixlen
                if ge is not None:
                    line += ["greater-equal", ge]
                if le is not None:
                    line += ["less-equal", le]
                yield tuple(line)
        for name, rules in sorted((routing.get("as_path_filters") or {}).items()):
            for rule in sorted(rules, key=lambda r: r["index"]):
                yield "ip as-path-filter", name, "index", rule["index"], rule["action"], rule["regex"]
        for name, rules in sorted((routing.get("community_filters") or {}).items()):
            for rule in sorted(rules, key=lambda r: r["index"]):
                yield "ip community-filter basic", name, "index", rule["index"], rule["action"], rule["community"]
