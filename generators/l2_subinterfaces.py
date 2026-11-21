from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import has_overlay, not_management, port_segments, subif_number


class L2Subinterfaces(PartialGenerator):
    """`mode l2` sub-interfaces that put a port (tagged or untagged) into a bridge domain.

    Numbering: tagged -> <port>.<bd_id>, untagged -> <port>.1. Owns every
    sub-interface on the leaf, so one no longer in NetBox is deleted.
    """

    TAGS = ["segments", "subinterfaces"]

    def acl_huawei(self, device: Any) -> str:
        if not has_overlay(device):
            return ""
        sub = f"*/{not_management(device, 'interfaces')}\\S+[.]\\d+/"
        return "\n".join([f"interface {sub} ~  %cant_delete=0", "    ~ %global", f"interface {sub}  %cant_delete=0", "    ~ %global"])

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not has_overlay(device):
            return
        for segment in port_segments(device):
            with self.block("interface", f"{segment.port}.{subif_number(segment, device)}", "mode l2"):
                if segment.vid is None:
                    yield "encapsulation untag"
                else:
                    yield "encapsulation dot1q vid", segment.vid
                yield "bridge-domain", segment.bd_id
