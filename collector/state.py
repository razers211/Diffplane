"""Read interface state and LLDP neighbours from a Huawei CE over NETCONF.

Paths verified on the lab switch (YunShan OS V300R023, see docs/): interface state
is `huawei-ifm` /ifm/interfaces/interface/dynamic, cumulative counters are its
mib-statistics (they count even with statistic-enable false: MEth0/0/0), and
LLDP is augmented under each main interface at lldp/session/neighbors/neighbor
(huawei-lldp). Read-only: <get> only.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

IFM = "urn:huawei:yang:huawei-ifm"
LLDP = "urn:huawei:yang:huawei-lldp"

_STATISTICS = """
      <mib-statistics>
        <receive-byte/><send-byte/>
        <receive-error-packet/><send-error-packet/>
        <receive-drop-packet/><send-drop-packet/>
      </mib-statistics>"""


def subtree(lldp: bool = True, main_only: bool = False, statistics: bool = True, bandwidth: bool = True) -> str:
    """Subtree filter with only the leaves the collector uses.
    main_only: a content match that leaves out sub-interfaces (RFC 6241, 6.2.5)."""
    neighbours = f'\n      <lldp xmlns="{LLDP}"><session><neighbors/></session></lldp>' if lldp else ""
    kind = "<class>main-interface</class>" if main_only else "<class/>"
    return f"""
<ifm xmlns="{IFM}">
  <interfaces>
    <interface>
      <name/>
      {kind}{"<index/>" if bandwidth else ""}
      <dynamic><oper-status/>{"<bandwidth/>" if bandwidth else ""}</dynamic>{_STATISTICS if statistics else ""}{neighbours}
    </interface>
  </interfaces>
</ifm>
"""


FILTER = subtree(lldp=True)          # everything: state, counters, LLDP neighbours
# counters every 5 s from main interfaces only (a test switch needed about
# 3.5 s for a counters <get> of all its interfaces); state and LLDP of all interfaces every 25 s.
COUNTERS_FILTER = subtree(lldp=False, main_only=True, bandwidth=False)
STATE_FILTER = subtree(lldp=True, statistics=False)

# Physical ports: the only interfaces whose counters the collector keeps:
# front-panel ports (10GE1/0/1, 100GE1/0/1, ...) and the management port.
PHYSICAL = re.compile(r"(\d*GE|MEth)\d+/\d+/\d+")


def is_physical(interface: str) -> bool:
    return PHYSICAL.fullmatch(interface) is not None


def has_bandwidth(interface: str) -> bool:
    """Ports and Eth-Trunks: what capacity is measured against."""
    return is_physical(interface) or re.fullmatch(r"Eth-Trunk\d+", interface) is not None


@dataclass(frozen=True)
class Neighbor:
    interface: str
    system_name: str
    port_id: str
    chassis_id: str
    port_description: str


# Counter name (Prometheus, without _total) -> huawei-ifm mib-statistics leaf.
COUNTERS = {
    "in_octets": "receive-byte",
    "out_octets": "send-byte",
    "in_errors": "receive-error-packet",
    "out_errors": "send-error-packet",
    "in_discards": "receive-drop-packet",
    "out_discards": "send-drop-packet",
}


@dataclass
class DeviceState:
    # interface name -> operationally up
    oper_up: dict[str, bool] = field(default_factory=dict)
    neighbors: list[Neighbor] = field(default_factory=list)
    # interface name -> counter name -> cumulative value (only counters the switch returned)
    counters: dict[str, dict[str, int]] = field(default_factory=dict)
    # interface name -> bits/s the switch reports (dynamic/bandwidth): a port's
    # speed (nominal while down; MEth0/0/0 up showed 100 M), a trunk's selected members (0 while down)
    bandwidth: dict[str, int] = field(default_factory=dict)
    # interface name -> ifIndex (huawei-ifm `index`: names a NetStream record's input port;
    # 10GE1/0/5 is 15 here and in `display interface`)
    ifindex: dict[str, int] = field(default_factory=dict)


def _text(element: ET.Element, ns: str, *path: str) -> str:
    found = element.find("/".join(f"{{{ns}}}{p}" for p in path))
    return (found.text or "").strip() if found is not None else ""


def parse(data_xml: str) -> DeviceState:
    """Parse the <data> reply to FILTER."""
    root = ET.fromstring(data_xml)
    state = DeviceState()
    for iface in root.iter(f"{{{IFM}}}interface"):
        name = _text(iface, IFM, "name")
        if not name:
            continue
        status = _text(iface, IFM, "dynamic", "oper-status")
        if status:
            state.oper_up[name] = status == "up"
        index = _text(iface, IFM, "index")
        if index.isdigit() and has_bandwidth(name):
            state.ifindex[name] = int(index)
        speed = _text(iface, IFM, "dynamic", "bandwidth")
        if speed.isdigit() and has_bandwidth(name):
            state.bandwidth[name] = int(speed)
        stats = iface.find(f"{{{IFM}}}mib-statistics")
        if stats is not None:
            values = {}
            for counter, leaf in COUNTERS.items():
                text = _text(stats, IFM, leaf)
                if text.isdigit():
                    values[counter] = int(text)
            if values:
                state.counters[name] = values
        for neighbor in iface.iter(f"{{{LLDP}}}neighbor"):
            state.neighbors.append(
                Neighbor(
                    interface=name,
                    system_name=_text(neighbor, LLDP, "system-name"),
                    port_id=_text(neighbor, LLDP, "port-id"),
                    chassis_id=_text(neighbor, LLDP, "chassis-id"),
                    port_description=_text(neighbor, LLDP, "port-description"),
                )
            )
    return state
