"""Control-plane state over NETCONF: BGP and EVPN peers, OSPF neighbours,
route counts per VPN instance, M-LAG/DFS, VXLAN tunnels (VTEP peers) and MAC counts; and
which ports edge-port protection acted on (collector/protection.py).

Paths read from a test switch's own replies and YANG (probe). Read-only: <get>.
A query the switch refuses (rpc-error, e.g. a model without M-LAG) leaves out that part only.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from collector import protection

NI = "urn:huawei:yang:huawei-network-instance"
BGP = "urn:huawei:yang:huawei-bgp"
OSPF = "urn:huawei:yang:huawei-ospfv2"
L3VPN = "urn:huawei:yang:huawei-l3vpn"
ROUTING = "urn:huawei:yang:huawei-routing"
MLAG = "urn:huawei:yang:huawei-m-lag"
NVO3 = "urn:huawei:yang:huawei-nvo3"
MAC = "urn:huawei:yang:huawei-mac"

# One subtree per module (ncclient takes one root element per filter).
QUERIES = {
    "instances": (
        f'<network-instance xmlns="{NI}"><instances><instance><name/>'
        f'<bgp xmlns="{BGP}"><base-process><peers><peer><address/><afs><af><type/><state><current-state/><remote-as/>'
        '<up-down-duration/><receive-prefix/><receive-active-prefix/><advertise-prefix/></state></af></afs></peer>'
        '</peers></base-process></bgp>'
        f'<ospfv2 xmlns="{OSPF}"><sites><site><neighbors/></site></sites></ospfv2>'
        f'<afs xmlns="{L3VPN}"><af><routing xmlns="{ROUTING}"><routing-manage><topologys><topology><routes>'
        '<ipv4-route-statistics/></routes></topology></topologys></routing-manage></routing></af></afs>'
        '</instance></instances></network-instance>'
    ),
    "mlag": f'<m-lag xmlns="{MLAG}"><negotiation-states/></m-lag>',
    "tunnels": f'<nvo3 xmlns="{NVO3}"><tunnels/></nvo3>',
    "macs": f'<mac xmlns="{MAC}"><mac-statistics/></mac>',
    "protection": protection.QUERY,   # ports storm control, loop detection or BPDU protection acted on
}

BGP_UP = "established"                 # huawei-bgp-type peer-state
OSPF_UP = "full"                       # OSPF's adjacency state; the switch's own spelling unverified (no neighbour on the test switch)
HEARTBEAT_OK = "ok"                    # huawei-m-lag heart-beat-state: ok | lost
MAC_KINDS = ("total", "dynamic", "dynamic-local", "dynamic-remote", "static", "black-hole")


@dataclass(frozen=True)
class BgpPeer:
    instance: str
    address: str
    family: str          # ipv4uni, evpn, ...
    state: str
    remote_as: str
    seconds: int | None  # in the current state (up-down-duration)
    received: int | None
    active: int | None
    advertised: int | None


@dataclass(frozen=True)
class OspfNeighbor:
    instance: str
    area: str
    interface: str
    router_id: str
    address: str
    state: str


@dataclass(frozen=True)
class Tunnel:
    source: str
    destination: str
    state: str


@dataclass
class ControlState:
    bgp: list[BgpPeer] = field(default_factory=list)
    ospf: list[OspfNeighbor] = field(default_factory=list)
    routes: dict[tuple[str, str], tuple[int, int]] = field(default_factory=dict)   # (instance, protocol) -> (total, active)
    dfs: dict[str, str] = field(default_factory=dict)          # role, state, heartbeat, causation (empty: no M-LAG)
    peer_links: dict[str, bool] = field(default_factory=dict)  # interface -> up
    mlag_ports: dict[str, bool] = field(default_factory=dict)  # Eth-TrunkN -> up
    tunnels: list[Tunnel] = field(default_factory=list)
    macs: dict[str, int] = field(default_factory=dict)         # kind -> entries, and "capacity"
    protection: dict[str, list["protection.Trip"]] = field(default_factory=dict)   # interface -> trips
    missing: set[str] = field(default_factory=set)             # queries the switch refused


def _t(element, ns: str, *path: str) -> str:
    found = element.find("/".join(f"{{{ns}}}{p}" for p in path))
    return (found.text or "").strip() if found is not None else ""


def _int(text: str) -> int | None:
    return int(text) if text.isdigit() else None


DURATION = re.compile(r"(\d+)([dhms])")


def seconds(text: str) -> int | None:
    """VRP's up-down-duration (`0168h37m` seen on a test switch; d/h/m/s read generically)."""
    parts = DURATION.findall(text or "")
    if not parts:
        return None
    unit = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    return sum(int(n) * unit[u] for n, u in parts)


def parse_instances(xml: str, state: ControlState) -> None:
    root = ET.fromstring(xml)
    for instance in root.iter(f"{{{NI}}}instance"):
        name = _t(instance, NI, "name")
        for peer in instance.iter(f"{{{BGP}}}peer"):
            address = _t(peer, BGP, "address")
            for af in peer.iter(f"{{{BGP}}}af"):
                st = af.find(f"{{{BGP}}}state")
                if st is None:
                    continue
                state.bgp.append(BgpPeer(
                    instance=name, address=address, family=_t(af, BGP, "type"), state=_t(st, BGP, "current-state"),
                    remote_as=_t(st, BGP, "remote-as"), seconds=seconds(_t(st, BGP, "up-down-duration")),
                    received=_int(_t(st, BGP, "receive-prefix")), active=_int(_t(st, BGP, "receive-active-prefix")),
                    advertised=_int(_t(st, BGP, "advertise-prefix"))))
        for neighbor in instance.iter(f"{{{OSPF}}}neighbor"):
            state.ospf.append(OspfNeighbor(
                instance=name, area=_t(neighbor, OSPF, "area-id"), interface=_t(neighbor, OSPF, "if-name"),
                router_id=_t(neighbor, OSPF, "router-id"), address=_t(neighbor, OSPF, "ip-addr"),
                state=_t(neighbor, OSPF, "state").lower()))
        for stat in instance.iter(f"{{{ROUTING}}}ipv4-route-statistic"):
            total, active = _int(_t(stat, ROUTING, "total-num")), _int(_t(stat, ROUTING, "active-num"))
            if total is not None and active is not None:
                state.routes[(name, _t(stat, ROUTING, "protocol-type"))] = (total, active)


def parse_mlag(xml: str, state: ControlState) -> None:
    root = ET.fromstring(xml)
    negotiation = root.find(f".//{{{MLAG}}}negotiation-state")
    if negotiation is None:
        return
    group = negotiation.find(f".//{{{MLAG}}}dfs-group-state")
    state.dfs = {"role": _t(negotiation, MLAG, "dfs-role"), "heartbeat": _t(negotiation, MLAG, "heart-beat-state"),
                 "state": _t(group, MLAG, "state") if group is not None else "",
                 "causation": _t(group, MLAG, "causation") if group is not None else ""}
    for link in negotiation.iter(f"{{{MLAG}}}peer-link-state"):
        state.peer_links[_t(link, MLAG, "interface-name")] = _t(link, MLAG, "port-state").upper() == "UP"
    for port in negotiation.iter(f"{{{MLAG}}}m-lag"):
        state.mlag_ports[_t(port, MLAG, "interface-name")] = _t(port, MLAG, "port-state").upper() == "UP"


def parse_tunnels(xml: str, state: ControlState) -> None:
    for tunnel in ET.fromstring(xml).iter(f"{{{NVO3}}}tunnel"):
        state.tunnels.append(Tunnel(_t(tunnel, NVO3, "source"), _t(tunnel, NVO3, "destination"),
                                    _t(tunnel, NVO3, "state").lower()))


def parse_macs(xml: str, state: ControlState) -> None:
    totals: dict[str, int] = {}
    for slot in ET.fromstring(xml).iter(f"{{{MAC}}}mac-statistic"):
        for kind in (*MAC_KINDS, "capacity"):
            value = _int(_t(slot, MAC, kind))
            if value is not None:
                totals[kind] = totals.get(kind, 0) + value
    state.macs = totals


def parse_protection(xml: str, state: ControlState) -> None:
    state.protection = protection.parse(xml)


PARSERS = {"instances": parse_instances, "mlag": parse_mlag, "tunnels": parse_tunnels, "macs": parse_macs,
           "protection": parse_protection}


def parse(replies: dict[str, str | None]) -> ControlState:
    """`replies`: query name -> its <data> reply, or None when the switch refused that query."""
    state = ControlState()
    for name, xml in replies.items():
        if xml is None:
            state.missing.add(name)
        else:
            PARSERS[name](xml, state)
    return state
