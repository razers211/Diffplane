import re
from collections.abc import Iterator
from typing import Any

from annet.generators import PartialGenerator

from generators.dci import uplink_ports
from generators.fabric import FRONT_PANEL_PORT, all_trunks, fabric_links, gateway_segments, has_vtep, port_segments

# The queue plan on Huawei's default DiffServ domain (DSCP 8-15 -> af1, 24-31 ->
# af3, ...): queues 5-7 (ef, cs6, cs7) strict priority, the switch's
# default and not displayed, so not written; queues 0-4 weighted.
DRR_QUEUES = "0 to 4"
WEIGHTS = {0: 30, 1: 5, 2: 15, 3: 35, 4: 15}   # be, scavenger (af1), af2, storage (af3), af4
# A mark rewrites DSCP; V300 stores it by name (`remark dscp 26` -> `af31`, 8 -> `cs1`, rounds 2 and 5).
MARKS = {"storage": "af31", "scavenger": "cs1"}
CLASSIFIER = "QOS-ANY"
PRECEDENCE = 5   # added by V300 to the policy's only pair when not given
PHYSICAL = re.compile(r"\d*GE\d+/\d+/\d+")


def document(device: Any) -> dict:
    return ((device.config_context or {}).get("fabric") or {}).get("qos") or {}


def enabled(device: Any) -> bool:
    return bool(document(device).get("enabled"))


def policy(mark: str) -> str:
    return f"QOS-{mark.upper()}"


def bridge_domain_marks(device: Any) -> dict[int, str]:
    """{bd_id: mark} for the marked segments whose bridge domain is on this switch (as BridgeDomains
    places them: a port's segment, or every routed one on a centralized gateway)."""
    if not enabled(device) or not has_vtep(device):
        return {}
    marks = document(device).get("segments") or {}
    here = {s.vni: s.bd_id for s in port_segments(device)} | {s.vni: s.bd_id for s in gateway_segments(device)}
    return {bd: marks[str(vni)] for vni, bd in here.items() if str(vni) in marks}


def ports(device: Any) -> tuple[list[str], set[str], list[str]]:
    """(physical front-panel ports, those that take `trust dscp`, Eth-Trunks). V300 refuses trust on
    a trunk member and on a routed port, and the schedule on an Eth-Trunk."""
    physical = sorted((i for i in device.interfaces if PHYSICAL.fullmatch(i.name) and i.type.value not in ("lag", "virtual")),
                      key=lambda i: _port_key(i.name))
    routed = {link.interface for link in fabric_links(device)} | uplink_ports(device)   # DCI uplinks
    layer2 = {i.name for i in physical if i.lag is None and i.name not in routed}
    return [i.name for i in physical], layer2, [t.name for t in all_trunks(device)]


def _port_key(name: str) -> tuple:
    speed, _, rest = name.partition("GE")
    return (int(speed or 0), *(int(n) for n in rest.split("/")))


class Qos(PartialGenerator):
    """QoS, while the fabric's QoS is on (Network › QoS), on every switch.

    Every physical front-panel port (used or not, trunk members and fabric links included; never
    MEth) gets the queue plan: `qos drr 0 to 4` and each DRR queue's weight. Layer-2 ports and
    every Eth-Trunk trust DSCP. A marked port or trunk gets the mark's traffic policy inbound
    (a marked segment's goes on its bridge domain: BridgeDomains). Each mark is a classifier
    matching everything, a behavior rewriting DSCP and a policy, written where a mark uses them.
    Forms as V300R025 stores them.

    Owns `trust` and `qos` on front-panel ports and Eth-Trunks by kind, and the traffic classifiers,
    behaviors and policies named QOS-* with their `traffic-policy QOS-*` lines; other traffic
    policies are left alone.
    """

    TAGS = ["qos"]

    def acl_huawei(self, device: Any) -> str:
        return "\n".join([
            "traffic classifier */QOS-\\S+/ ~ %cant_delete=0",
            "    ~ %global",
            "traffic behavior */QOS-\\S+/ %cant_delete=0",
            "    ~ %global",
            "traffic policy */QOS-\\S+/ %cant_delete=0",
            "    ~ %global",
            f"interface {FRONT_PANEL_PORT} %cant_delete=1",
            "    trust ~",
            "    qos ~",
            "    traffic-policy */QOS-\\S+/ ~",
            "interface */Eth-Trunk\\d+/ %cant_delete=1",
            "    trust ~",
            "    traffic-policy */QOS-\\S+/ ~",
        ])

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not enabled(device):
            return
        physical, layer2, trunks = ports(device)
        wanted = (document(device).get("ports") or {}).get(device.name) or {}
        marked = {p: m for p, m in wanted.items() if m in MARKS and (p in layer2 or p in trunks)}
        used = sorted(set(marked.values()) | set(bridge_domain_marks(device).values()), key=list(MARKS).index)
        if used:
            with self.block("traffic classifier", CLASSIFIER, "type or"):
                yield "if-match any"
        for mark in used:
            with self.block("traffic behavior", policy(mark)):
                yield "remark dscp", MARKS[mark]
        for mark in used:
            with self.block("traffic policy", policy(mark)):
                yield "classifier", CLASSIFIER, "behavior", policy(mark), "precedence", PRECEDENCE
        for port in physical:
            with self.block("interface", port):
                if port in layer2:
                    yield "trust dscp"
                yield "qos drr", DRR_QUEUES
                for queue, weight in WEIGHTS.items():
                    yield "qos queue", queue, "drr weight", weight
                if port in marked:
                    yield "traffic-policy", policy(marked[port]), "inbound"
        for trunk in trunks:
            with self.block("interface", trunk):
                yield "trust dscp"
                if trunk in marked:
                    yield "traffic-policy", policy(marked[trunk]), "inbound"
