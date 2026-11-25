from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import own_loopback, fabric_links, fabric_value, loopback, pip_layout
from generators.loopbacks import owned_loopbacks
from generators.ospf_auth import KEYCHAIN, ospf_auth

PROCESS = 1
AREA = "0.0.0.0"


class Ospf(PartialGenerator):
    """OSPF underlay: one process, area 0, on the loopbacks and fabric links, with BFD.

    Router ID is the device's LoopBack0 address. Interfaces join the area with
    `ospf enable` (no `network` statements), so ownership stays per interface.
    Verified on the lab switch (V300R023).
    """

    TAGS = ["underlay", "ospf"]

    def acl_huawei(self, device: Any) -> str:
        interfaces = owned_loopbacks(device) + [link.interface for link in fabric_links(device)]
        process = fabric_value(device, "ospf_process", PROCESS)
        return "\n".join(
            [
                "bfd$",
                f"ospf {process} ~",
                "    ~ %global",
                *(f"interface {name}\n    ospf ~" for name in interfaces),
            ]
        )

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        router_id = own_loopback(device)
        if router_id is None:
            return
        process = fabric_value(device, "ospf_process", PROCESS)
        yield "bfd"
        with self.block("ospf", process, "router-id", str(router_id.ip)):
            # imported fabrics: BFD on every OSPF interface and LSA arrival timers, adopted
            if (bfd := fabric_value(device, "ospf_bfd_all_interfaces", None)) is not None:
                yield "bfd all-interfaces enable"
                yield "bfd all-interfaces min-tx-interval", bfd[0], "min-rx-interval", bfd[1]
            if (lsa := fabric_value(device, "ospf_lsa_arrival", None)) is not None:
                yield "lsa-arrival-interval intelligent-timer", *lsa
            with self.block("area", AREA):
                pass
        for name in owned_loopbacks(device):
            if pip_layout(device) and name == "LoopBack1":
                continue   # the PIP is reached over the M-LAG bypass only (imported fabrics)
            if loopback(device, name) is not None:
                with self.block("interface", name):
                    yield "ospf enable", process, "area", AREA
        for link in fabric_links(device):
            with self.block("interface", link.interface):
                yield "ospf network-type p2p"
                if (hold := fabric_value(device, "link_hold_max_cost", None)) is not None:
                    yield "ospf peer hold-max-cost timer", hold
                yield "ospf enable", process, "area", AREA
                if ospf_auth(device).get("enabled"):
                    yield "ospf authentication-mode keychain", KEYCHAIN
                if fabric_value(device, "link_ospf_bfd", True):
                    yield "ospf bfd enable"
