"""Edge-port protection state over NETCONF: which ports storm
control, loop detection or BPDU protection has acted on, read with the control-plane poll (25 s).

One <get> of every interface's `storm-control-state` (huawei-storm-control), `loopback-detect-infos`
(huawei-loopdetect) and `error-down` (huawei-ifm); paths from the R025 TOR-H YANG, the empty replies
recorded on a test switch. A tripped state has not been seen on a switch: nothing was
provoked in the lab, so the values below are the YANG's enumerations.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass

IFM = "urn:huawei:yang:huawei-ifm"
STORM = "urn:huawei:yang:huawei-storm-control"
LOOP = "urn:huawei:yang:huawei-loopdetect"

QUERY = (f'<ifm xmlns="{IFM}"><interfaces><interface><name/>'
         f'<storm-control xmlns="{STORM}"><storm-control-state/></storm-control>'
         f'<loopdetect xmlns="{LOOP}"><loopback-detect-infos/></loopdetect>'
         '<error-down/></interface></interfaces></ifm>')

CAUSES = ("storm-control", "loopback-detect", "bpdu-protection")   # error-down causes protection makes


@dataclass(frozen=True)
class Trip:
    cause: str              # storm-control, loopback-detect, bpdu-protection
    state: str              # error-down, block, suppress, alarm, ...
    detail: str = ""        # storm control: the packet type
    reopen: int | None = None   # seconds left before error-down auto-recovery reopens the port


def _t(element, ns: str, *path: str) -> str:
    found = element.find("/".join(f"{{{ns}}}{p}" for p in path))
    return (found.text or "").strip() if found is not None else ""


def parse(xml: str) -> dict[str, list[Trip]]:
    """interface -> what protection is doing to it now (only interfaces where it acts)."""
    found: dict[str, list[Trip]] = {}
    for iface in ET.fromstring(xml).iter(f"{{{IFM}}}interface"):
        name = _t(iface, IFM, "name")
        trips: list[Trip] = []
        down_cause = _t(iface, IFM, "error-down", "cause")
        remainder = _t(iface, IFM, "error-down", "remainder-time")
        reopen = int(remainder) if remainder.isdigit() else None
        punish = _t(iface, STORM, "storm-control", "storm-control-state", "punish")
        if punish and punish != "normal":
            trips.append(Trip("storm-control", punish, _t(iface, STORM, "storm-control", "storm-control-state", "packet-type"),
                              reopen if down_cause == "storm-control" else None))
        status = _t(iface, LOOP, "loopdetect", "loopback-detect-infos", "status")
        if status and status != "normal":
            trips.append(Trip("loopback-detect", status, reopen=reopen if down_cause == "loopback-detect" else None))
        if down_cause in CAUSES and down_cause not in {t.cause for t in trips}:
            trips.append(Trip(down_cause, "error-down", reopen=reopen))
        if trips:
            found[name] = trips
    return found
