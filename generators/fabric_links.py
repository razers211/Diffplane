from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import description, fabric_links, fabric_value, ip_with_mask

FABRIC_MTU = 9216


class FabricLinks(PartialGenerator):
    """Routed spine-leaf ports: L3 mode, description, MTU and the /30 from NetBox.

    Owns only the ports that are fabric links in NetBox; every other port is left alone.
    Verified on the lab switch (V300R023).
    """

    TAGS = ["underlay", "fabric-links"]

    def acl_huawei(self, device: Any) -> str:
        return "\n".join(
            f"interface {link.interface}\n"
            "    undo portswitch\n"
            "    description ~\n"
            "    mtu *\n"
            "    ip address ~"
            for link in fabric_links(device)
        )

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        for link in fabric_links(device):
            with self.block("interface", link.interface):
                yield "undo portswitch"
                yield "description", description(device, link.interface, f"to {link.peer_device} {link.peer_interface}")
                if (mtu := fabric_value(device, "link_mtu", FABRIC_MTU)) is not None:   # imported fabrics: none
                    yield "mtu", mtu
                if link.address is None:   # display form unverified (accepted on the test switch)
                    yield "ip address unnumbered interface LoopBack0"
                else:
                    yield "ip address", ip_with_mask(link.address)
