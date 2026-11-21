from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import device_value, has_overlay, local_services, management, vlan_list
from generators.mlag_bypass import bypass_vlan


class Vlans(PartialGenerator):
    """The device's `vlan batch`: the M-LAG bypass VLAN, the firewall service VLANs, and a
    management bridge domain's VLAN, kept as the switch has it (generators/fabric.py management).

    One generator owns the line: Annet refuses two generators that may both delete the
    same line (AclNotExclusiveError), and VRP keeps a single
    merged `vlan batch` anyway.
    """

    TAGS = ["vlans"]

    def acl_huawei(self, device: Any) -> str:
        return "vlan batch ~" if has_overlay(device) else ""

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not has_overlay(device):
            return
        vlans = {s.vlan for s in local_services(device)} | set(management(device, "vlans"))
        vlans |= set(device_value(device, "kept_vlans", []) or [])   # unused VLANs the import kept (imported fabrics)
        if (bypass := bypass_vlan(device)) is not None:
            vlans.add(bypass)
        if vlans:
            yield "vlan batch", vlan_list(vlans)
