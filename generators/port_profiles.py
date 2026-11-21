import re
from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.dci import uplink_ports
from generators.fabric import _context, fabric_links, management
from seed.ports import is_copper

PHYSICAL = re.compile(r"\d*GE\d+/\d+/\d+")
DEFAULT_FRAME = 9216   # every port's and Eth-Trunk's jumbo frame size unless set (a test switch)
SPEEDS = (10, 100, 1000, 10000)   # Mbit/s a copper port takes with `speed` (the switch's help)


def enabled(device: Any) -> bool:
    return bool(((_context(device).get("fabric") or {}).get("port_profiles") or {}).get("enabled"))


def _tags(iface: Any) -> set[str]:
    return {t.slug for t in iface.tags or []}


def server_ports(device: Any) -> tuple[list[Any], list[Any]]:
    """(physical server ports, server Eth-Trunks): the ports a port profile applies to. Never MEth, a
    sub-interface, a trunk member (its trunk is), the peer-link, a routed or unnumbered fabric link, a
    DCI uplink, or a port carrying the management network (management plane)."""
    routed = {link.interface for link in fabric_links(device)} | uplink_ports(device)
    managed = {name.split(".")[0] for name in management(device, "interfaces")}

    def server(i: Any) -> bool:
        return (PHYSICAL.fullmatch(i.name) is not None or i.type.value == "lag") and not i.ip_addresses \
            and i.name not in routed | managed and not _tags(i) & {"unnumbered", "peer-link"}

    ports = [i for i in device.interfaces if server(i)]
    return ([i for i in ports if i.type.value not in ("lag", "virtual") and i.lag is None],
            [i for i in ports if i.type.value == "lag" and i.name.startswith("Eth-Trunk")])


class PortProfiles(PartialGenerator):
    """Port settings of server ports and server Eth-Trunks, while the
    fabric's port settings are managed (turned on with an adoption of what the switches have): the
    description of a physical port (a trunk's is Mlag's or Trunks'), `shutdown` when NetBox has the
    interface disabled, `jumboframe enable <n>` when NetBox's MTU holds a frame size (Expert; none:
    the switch's default 9216), and on a copper port with a fixed speed in NetBox (kbit/s; none:
    negotiated, the default) `negotiation disable` then `speed <Mbit/s>` (V300 refuses `speed` while
    negotiating; stored in that order: a lab test under commit trial). A hand-made
    `speed auto <list>` is not owned (its undo form is unseen). Forms accepted on a test switch; Annet's own
    rules undo them as V300 offers (`undo description`, `undo shutdown`, `undo jumboframe enable`).

    Owns those lines by kind on exactly these ports, and nothing while the fabric's port settings
    are not managed: hand-made ones elsewhere are left alone.
    """

    TAGS = ["port-profiles"]

    def acl_huawei(self, device: Any) -> str:
        if not enabled(device):
            return ""
        physical, trunks = server_ports(device)
        lines = []
        for iface in physical:
            lines += [f"interface {iface.name} %cant_delete=1", "    description ~", "    shutdown", "    jumboframe enable ~"]
            if is_copper(_type(iface)):
                lines += ["    negotiation disable"] + [f"    speed {s}" for s in SPEEDS]
        for iface in trunks:
            lines += [f"interface {iface.name} %cant_delete=1", "    shutdown", "    jumboframe enable ~"]
        return "\n".join(lines)

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not enabled(device):
            return
        physical, trunks = server_ports(device)
        for iface in physical + trunks:
            lines = []
            if iface in physical and iface.description:
                lines.append(("description", iface.description))
            if not iface.enabled:
                lines.append(("shutdown",))
            if iface.mtu:   # set only by the GUI's Expert frame size or an adoption that found the line
                lines.append(("jumboframe enable", iface.mtu))
            if iface in physical and iface.speed and is_copper(_type(iface)):
                lines += [("negotiation disable",), ("speed", iface.speed // 1000)]
            if lines:
                with self.block("interface", iface.name):
                    yield from lines


def _type(iface: Any) -> str | None:
    """The NetBox interface type (learned from the switch's own port hardware)."""
    return getattr(getattr(iface, "type", None), "value", None)
