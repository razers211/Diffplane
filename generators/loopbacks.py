from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import ip_with_mask, has_overlay, loopback, pip_layout


def owned_loopbacks(device: Any) -> list[str]:
    """LoopBack0 on every device; LoopBack1 (shared VTEP address) on leaves and on a gateway
    pair of spines; in the PIP layout LoopBack1 is the PIP and LoopBack2 the VTEP."""
    if not has_overlay(device):
        return ["LoopBack0"]
    return ["LoopBack0", "LoopBack1", "LoopBack2"] if pip_layout(device) else ["LoopBack0", "LoopBack1"]


class Loopbacks(PartialGenerator):
    """Loopback addresses from NetBox. Owns only their `ip address` lines."""

    TAGS = ["underlay", "loopbacks"]

    def acl_huawei(self, device: Any) -> str:
        return "\n".join(f"interface {name}\n    ip address ~" for name in owned_loopbacks(device))

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        for name in owned_loopbacks(device):
            address = loopback(device, name)
            if address is None:
                continue
            with self.block("interface", name):
                yield "ip address", ip_with_mask(address)
