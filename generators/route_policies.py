from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import device_routing, owned


class RoutePolicies(PartialGenerator):
    """Route-policies from the routing intent: one block per node.

    Owns only the names in intent or retired. Verified on a test switch:
    `route-policy <n> permit|deny node <i>`, `if-match ip-prefix`, `apply local-preference`.
    """

    TAGS = ["routing", "policies"]

    def acl_huawei(self, device: Any) -> str:
        return "\n".join(f"route-policy {n} ~\n    ~ %global" for n in owned(device, "route_policies"))

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        routing = device_routing(device)
        lists = routing.get("prefix_lists") or {}
        for name, nodes in sorted((routing.get("route_policies") or {}).items()):
            for node in sorted(nodes, key=lambda n: n["node"]):
                with self.block("route-policy", name, node["action"], "node", node["node"]):
                    if match := node.get("match_ip_prefix"):
                        if ":" in str((lists.get(match) or [{}])[0].get("prefix", "")):
                            yield "if-match ipv6 address prefix-list", match   # an IPv6 list
                        else:
                            yield "if-match ip-prefix", match
                    if node.get("apply_local_preference") is not None:
                        yield "apply local-preference", node["apply_local_preference"]
