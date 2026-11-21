from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import check_vpn, device_routing, owned


class OspfProcesses(PartialGenerator):
    """OSPF processes other than the underlay (process 1, Ospf) from the routing intent,
    each in a platform VPN instance, with its areas.

    Owns exactly the process numbers in intent or retired. Verified on a test switch:
    `ospf <n> router-id <ip> vpn-instance <v>` with `area 0.0.0.0`.
    """

    TAGS = ["routing", "ospf"]

    def acl_huawei(self, device: Any) -> str:
        numbers = sorted({p["process"] for p in owned(device, "ospf")})
        return "\n".join(f"ospf {n} ~\n    ~ %global" for n in numbers)

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        for process in sorted(device_routing(device).get("ospf") or [], key=lambda p: p["process"]):
            check_vpn(device, process["vpn"], f"OSPF process {process['process']}")
            with self.block("ospf", process["process"], "router-id", process["router_id"], "vpn-instance",
                            process["vpn"]):
                for area in process.get("areas") or []:
                    with self.block("area", area):
                        pass
        # Blocks are emitted as they open; a yield makes this a generator, as Annet requires.
        yield from ()
