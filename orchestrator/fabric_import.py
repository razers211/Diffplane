"""Import an existing fabric: from the switches' running configs to the
platform's model.

The operator gives the management addresses; each switch is read over SSH (orchestrator/
ssh_read.py) and parsed (orchestrator/import_parse.py). This module puts the switches
together: roles, M-LAG pairs, fabric links (LLDP, else the shared /30 or /31, else the
port description), addresses, pools to propose, and the switches the given ones point at
but that were not given (a planned M-LAG peer or spine): those become planned
placeholders, named from the descriptions where they say so. Values the switches use that
differ from what the platform derives are adopted (`overrides`, only where they differ);
what the platform cannot model is listed per switch as a misfit. Nothing is written here.
"""

import ipaddress
import re
from collections import Counter
from dataclasses import dataclass, field

from generators.fabric import is_segment_vpn, mlag_mac, segment_vpn, vlan_list
from generators.fabric_links import FABRIC_MTU
from orchestrator.import_parse import MANAGEMENT_VPN, PORT, Switch, tree, vlan_numbers

DFS_GROUP = 1
OSPF_PROCESS = 1
OSPF_AREA = "0.0.0.0"
NVE = "Nve1"
LINK_DESCRIPTION = re.compile(r"to (\S+) (\S+)$")
PEER_LINK_DESCRIPTION = re.compile(r"peer-link to (\S+)$")


class ImportError_(ValueError):
    """The switches cannot be imported as given (the operator must change the input)."""


@dataclass
class Device:
    name: str
    model: str | None
    role: str | None                 # spine, leaf, border-leaf; None: unknown
    management: str | None = None    # given address with mask; None: a planned placeholder
    loopback: ipaddress.IPv4Interface | None = None
    vtep: ipaddress.IPv4Interface | None = None
    pip: ipaddress.IPv4Interface | None = None   # the PIP layout: own PIP on LoopBack1, VTEP on LoopBack2

    @property
    def planned(self) -> bool:
        return self.management is None


@dataclass
class Pair:
    devices: tuple[str, str]           # sorted
    peer_link_trunk: int
    peer_link_members: tuple[str, ...]
    mlag_trunks: dict[int, tuple[str, ...]] = field(default_factory=dict)
    bypass: dict[str, ipaddress.IPv4Interface] = field(default_factory=dict)   # device -> bypass Vlanif address
    cabling: tuple[tuple[str, str], ...] = ()   # (first device's member, second's) when LLDP sees them crossed


@dataclass
class Link:
    spine: str
    spine_port: str
    leaf: str
    leaf_port: str
    spine_address: ipaddress.IPv4Interface | None    # None with unnumbered
    leaf_address: ipaddress.IPv4Interface | None
    unnumbered: bool = False
    found_by: str = "lldp"                           # lldp, subnet, description
    members: tuple[tuple[str, str], ...] = ()        # an Eth-Trunk link (imported fabrics): (spine port, leaf port) cabled

    @property
    def network(self) -> ipaddress.IPv4Network | None:
        return self.leaf_address.network if self.leaf_address else None


@dataclass
class Segment:
    vni: int
    bd: int
    name: str
    gateway: ipaddress.IPv4Interface | None = None   # routed: the gateway (first host of its subnet)
    firewall: str | None = None
    peers: set[int] = field(default_factory=set)
    vrf: str | None = None                           # its VPN instance on the switches


@dataclass
class Service:
    name: str
    devices: tuple[str, ...]          # one leaf, or both leaves of a pair (sorted)
    port: str | None                  # None: its VLAN rides VLAN trunks (an imported fabric's firewall contexts)
    vlan: int
    address: ipaddress.IPv4Interface  # the Vlanif's (a pair's: the shared M-LAG address)
    next_hop: str | None
    asn: int | None
    kind: str                         # firewall or isp
    addresses: dict[str, str] = field(default_factory=dict)   # a pair's: each leaf's main address


@dataclass
class ImportPlan:
    asn: int | None
    architecture: str
    gateway_pair: tuple[str, str] | None
    bypass_vlan: int | None
    gateway_mac: str | None
    ntp_servers: tuple[str, ...]
    ntp_vpn: str | None
    snmp_location: str | None
    snmp_contact: str | None
    devices: dict[str, Device]
    pairs: list[Pair]
    links: list[Link]
    pools: dict[str, str | None]
    overrides: dict
    misfits: dict[str, list[str]]
    segments: dict[int, Segment] = field(default_factory=dict)
    attachments: list[tuple[str, str, int, int | None]] = field(default_factory=list)   # device, port, vni, vid
    services: dict[str, Service] = field(default_factory=dict)
    trunks: list[tuple[str, int, tuple[str, ...]]] = field(default_factory=list)       # single Eth-Trunks

    def misfit(self, device: str, text: str) -> None:
        self.misfits.setdefault(device, [])
        if text not in self.misfits[device]:
            self.misfits[device].append(text)


def other_host(address: ipaddress.IPv4Interface) -> ipaddress.IPv4Interface:
    """The far end of a /30 or /31 point-to-point subnet."""
    net = address.network
    hosts = [h for h in (net if net.prefixlen == 31 else net.hosts()) if h != address.ip]
    if len(hosts) != 1:
        raise ValueError(f"{address} is not a point-to-point subnet")
    return ipaddress.IPv4Interface(f"{hosts[0]}/{net.prefixlen}")


def nve_mac(vtep: ipaddress.IPv4Address) -> str:
    digits = "0200" + vtep.packed.hex()
    return "-".join(digits[i:i + 4] for i in range(0, 12, 4))


def _vtep_and_pip(switch) -> tuple:
    """(VTEP, PIP): the PIP layout when Nve1's `source` is LoopBack2's address and its
    `pip-source` LoopBack1's (imported fabrics), else the VTEP on LoopBack1."""
    one, two = switch.loopback("LoopBack1"), switch.loopback("LoopBack2")
    nve = switch.nve or {}
    if one and two and nve.get("source") == str(two.ip) and nve.get("pip_source") == str(one.ip):
        return two, one
    return one, None


def covering(addresses: list[ipaddress.IPv4Network], at_most: int = 24) -> str | None:
    """The smallest prefix, /`at_most` or larger, holding every given network."""
    if not addresses:
        return None
    length = min(at_most, *(a.prefixlen for a in addresses))
    while length >= 8:
        net = addresses[0].supernet(new_prefix=length)
        if all(a.subnet_of(net) for a in addresses):
            return str(net)
        length -= 1
    return None


def _role(switch: Switch) -> str | None:
    """A route reflector is a spine; a VTEP is a leaf. The operator may change it (border-leaf)."""
    if switch.bgp and any("reflect-client" in rows for peer in switch.bgp.peers.values()
                          for rows in peer.families.get("evpn", [])):
        return "spine"
    if switch.nve is not None:
        return "leaf"
    return None


def _routed_ports(switch: Switch):
    for iface in switch.interfaces.values():
        if PORT.fullmatch(iface.name) and iface.routed and (iface.address or iface.unnumbered) and iface.vpn is None:
            yield iface
        elif _link_trunk(iface):
            yield iface


def _link_trunk(iface) -> bool:
    """An unnumbered fabric link over an Eth-Trunk (an imported switch)."""
    return re.fullmatch(r"Eth-Trunk\d+", iface.name) is not None and iface.routed and iface.unnumbered is not None \
        and iface.vpn is None


def _members(switch: Switch, trunk: str) -> list[str]:
    number = int(trunk.removeprefix("Eth-Trunk"))
    return sorted(i.name for i in switch.interfaces.values() if i.trunk == number)


def assemble(switches: dict[str, tuple[Switch, str]], roles: dict[str, str] | None = None,
             architecture: str | None = None, gateway_pair: list[str] | None = None,
             drop: list[int] | None = None) -> ImportPlan:
    """`switches`: sysname -> (parsed switch, management address with mask); the operator's
    roles (e.g. border-leaf) and architecture where the configs cannot tell; `drop`: bridge-domain
    IDs the operator leaves out (not imported, so the platform would remove them)."""
    roles = roles or {}
    plan = ImportPlan(asn=None, architecture="symmetric", gateway_pair=None, bypass_vlan=None, gateway_mac=None,
                      ntp_servers=(), ntp_vpn=None, snmp_location=None, snmp_contact=None, devices={}, pairs=[],
                      links=[], pools={}, overrides={}, misfits={})
    by_loopback: dict[str, str] = {}
    for name, (switch, management) in sorted(switches.items()):
        role = roles.get(name) or _role(switch)
        if role is None:
            plan.misfit(name, "neither a route reflector (spine) nor a VTEP (leaf): give its role")
        loopback = switch.loopback("LoopBack0")
        vtep, pip = _vtep_and_pip(switch)
        plan.devices[name] = Device(name, switch.model, role, management, loopback, vtep, pip)
        if loopback is not None:
            by_loopback[str(loopback.ip)] = name
        else:
            plan.misfit(name, "no LoopBack0 address")
        if switch.model is None:
            plan.misfit(name, "model not found in `display version`")

    _fabric_settings(plan, switches)
    _pairs(plan, switches, by_loopback)
    _links(plan, switches)
    _spine_loopbacks(plan, switches, by_loopback)
    _values(plan, switches)
    _underlay(plan, switches)
    if architecture is not None:
        choose(plan, architecture, tuple(gateway_pair or ()))
    _tenants(plan, switches, architecture is not None, drop or ())
    _pools(plan)
    return plan


def _fabric_settings(plan: ImportPlan, switches) -> None:
    asns = {s.bgp.asn for s, _ in switches.values() if s.bgp}
    if len(asns) > 1:
        raise ImportError_(f"the switches run BGP in different ASes ({', '.join(map(str, sorted(asns)))}): the "
                           "platform models one fabric ASN (iBGP EVPN)")
    plan.asn = next(iter(asns), None)
    for name, (switch, _) in sorted(switches.items()):
        if plan.ntp_servers and (switch.ntp_servers, switch.ntp_vpn) != (plan.ntp_servers, plan.ntp_vpn):
            plan.misfit(name, "NTP servers differ from the other switches'")
        elif not plan.ntp_servers:
            plan.ntp_servers, plan.ntp_vpn = switch.ntp_servers, switch.ntp_vpn
        for key in ("location", "contact"):
            current = getattr(plan, f"snmp_{key}")
            value = switch.snmp[key]
            if current is None:
                setattr(plan, f"snmp_{key}", value)
            elif value != current:
                plan.misfit(name, f"SNMP {key} differs from the other switches'")
        bypass = [i for i in switch.interfaces.values() if i.bypass]
        for iface in bypass:
            vlan = int(iface.name.removeprefix("Vlanif"))
            if plan.bypass_vlan not in (None, vlan):
                plan.misfit(name, f"M-LAG bypass VLAN {vlan} differs from the other pairs' ({plan.bypass_vlan})")
            plan.bypass_vlan = plan.bypass_vlan or vlan


def _placeholder(plan: ImportPlan, name: str, model: str | None, role: str | None) -> Device:
    device = plan.devices.get(name)
    if device is None:
        device = plan.devices[name] = Device(name, model, role)
    return device


def _pairs(plan: ImportPlan, switches, by_loopback: dict[str, str]) -> None:
    for name, (switch, management) in sorted(switches.items()):
        if switch.dfs is not None and switch.dfs.vpn is not None:
            # imported fabrics: the heartbeat between the MEth addresses, adopted when it is exactly that
            if switch.dfs.vpn == MANAGEMENT_VPN and switch.dfs.source == management.split("/")[0] \
                    and any(m.split("/")[0] == switch.dfs.peer for n, (_, m) in switches.items() if n != name):
                _device_override(plan, name)["heartbeat"] = {"source": switch.dfs.source, "vpn": switch.dfs.vpn,
                                                             "peer": switch.dfs.peer}
            else:
                plan.misfit(name, f"M-LAG heartbeat in VPN instance {switch.dfs.vpn} ({switch.dfs.source} to "
                                  f"{switch.dfs.peer}): the platform uses LoopBack0, or both MEth addresses")
    done: set[str] = set()
    for name, (switch, _) in sorted(switches.items()):
        if switch.dfs is None or name in done:
            continue
        if switch.dfs.group != DFS_GROUP:
            plan.overrides.setdefault("fabric", {})["dfs_group"] = switch.dfs.group
        peer_link = next((i for i in switch.interfaces.values() if i.peer_link), None)
        if peer_link is None:
            plan.misfit(name, "DFS group without a peer-link")
            continue
        peer = by_loopback.get(switch.dfs.peer or "")
        if switch.dfs.vpn is not None:
            peer = next((n for n, (_, m) in switches.items() if m.split("/")[0] == switch.dfs.peer), None)
        if peer is None:
            described = PEER_LINK_DESCRIPTION.match(peer_link.description or "")
            peer = described.group(1) if described else f"{name}-peer"
            if peer in switches:
                plan.misfit(name, f"DFS peer {switch.dfs.peer} is not {peer}'s LoopBack0")
                continue
            device = _placeholder(plan, peer, switch.model, plan.devices[name].role)
            if switch.dfs.peer and switch.dfs.vpn is None:
                device.loopback = ipaddress.IPv4Interface(f"{switch.dfs.peer}/32")
            device.vtep = plan.devices[name].vtep
        trunk = int(peer_link.name.removeprefix("Eth-Trunk"))
        members = tuple(sorted(i.name for i in switch.interfaces.values() if i.trunk == trunk))
        mlag = {i.mlag: tuple(sorted(m.name for m in switch.interfaces.values()
                                     if m.trunk == int(i.name.removeprefix("Eth-Trunk"))))
                for i in switch.interfaces.values() if i.mlag is not None}
        for number in [n for n, members in mlag.items() if not members]:
            plan.misfit(name, f"M-LAG {number} has no member ports: left out")
            del mlag[number]
        for number, iface in ((i.mlag, i) for i in switch.interfaces.values() if i.mlag is not None):
            if int(iface.name.removeprefix("Eth-Trunk")) != number:
                plan.misfit(name, f"{iface.name} has M-LAG ID {number}: the platform uses the trunk number")
        pair = Pair(tuple(sorted((name, peer))), trunk, members, mlag)
        if peer in switches:   # both read: they must agree
            other = switches[peer][0]
            other_link = next((i for i in other.interfaces.values() if i.peer_link), None)
            other_members = tuple(sorted(i.name for i in other.interfaces.values()
                                         if other_link and i.trunk == int(other_link.name.removeprefix("Eth-Trunk"))))
            if other_link is None or other_link.name != peer_link.name or other_members != members:
                plan.misfit(peer, f"peer-link differs from {name}'s ({peer_link.name}: {', '.join(members)})")
            other_mlag = {i.mlag: tuple(sorted(m.name for m in other.interfaces.values()
                                               if m.trunk == int(i.name.removeprefix("Eth-Trunk"))))
                          for i in other.interfaces.values() if i.mlag is not None}
            for number in [n for n, members in other_mlag.items() if not members]:
                plan.misfit(peer, f"M-LAG {number} has no member ports: left out")
                del other_mlag[number]
            if other_mlag != mlag:
                plan.misfit(peer, f"M-LAG trunks differ from {name}'s")
            # members cabled to another member than their namesake (an imported fabric's spines: 47 to 48)
            seen = {local: port for local, port, device in switch.lldp if device == peer and local in members}
            if set(seen) == set(members) and set(seen.values()) == set(members) \
                    and any(a != b for a, b in seen.items()):
                pair.cabling = tuple(sorted(seen.items() if name == pair.devices[0]
                                            else ((b, a) for a, b in seen.items())))
        for device in pair.devices:
            if device in switches:
                bypass = next((i for i in switches[device][0].interfaces.values() if i.bypass), None)
                if bypass is not None and bypass.address is not None:
                    pair.bypass[device] = bypass.address
        if len({a.network for a in pair.bypass.values()}) > 1:   # an imported switch: .11/31 and .12/31
            for device, address in sorted(pair.bypass.items()):
                plan.misfit(device, f"M-LAG bypass {address} is not in the same subnet as its peer's "
                                    f"({', '.join(sorted(map(str, pair.bypass.values())))}): new addresses proposed")
            pair.bypass.clear()
        for device in pair.devices:
            if device not in pair.bypass and len(pair.bypass) == 1:
                pair.bypass[device] = other_host(next(iter(pair.bypass.values())))
        plan.pairs.append(pair)
        done.update(pair.devices)
        if plan.devices[name].vtep is not None and peer in switches \
                and plan.devices[peer].vtep != plan.devices[name].vtep:
            plan.misfit(peer, f"VTEP address differs from {name}'s: an M-LAG pair shares one")
    paired = [plan.devices[d] for p in plan.pairs for d in p.devices if d in switches]
    if any(d.pip for d in paired):   # one layout per fabric
        for device in paired:
            if device.pip is None:
                plan.misfit(device.name, "not in the PIP layout of the other M-LAG switches "
                                         "(PIP on LoopBack1, VTEP on LoopBack2)")


def _links(plan: ImportPlan, switches) -> None:
    seen: set[tuple[str, str]] = set()
    for name, (switch, _) in sorted(switches.items()):
        lldp = {local: (port, device) for local, port, device in switch.lldp}
        for iface in _routed_ports(switch):
            if (name, iface.name) in seen:
                continue
            far, found_by, cabled = None, None, []
            neighbour = lldp.get(iface.name)
            if _link_trunk(iface):   # its members' neighbours, all members of one trunk there
                ends = {lldp.get(m) for m in _members(switch, iface.name)}
                fars = {(d, f"Eth-Trunk{switches[d][0].interfaces[p].trunk}") for p, d in ends - {None}
                        if d in switches and p in switches[d][0].interfaces and switches[d][0].interfaces[p].trunk}
                if None in ends or len(fars) != 1:
                    plan.misfit(name, f"{iface.name}: its members' LLDP neighbours are not the members of one "
                                      "Eth-Trunk of a read switch")
                    continue
                far, found_by = fars.pop(), "lldp"
                cabled = [(m, lldp[m][0]) for m in _members(switch, iface.name)]
                neighbour = None
            if neighbour and neighbour[1] in switches:
                far, found_by = (neighbour[1], neighbour[0]), "lldp"
            elif iface.address is not None:
                for other, (candidate, _) in switches.items():
                    if other == name:
                        continue
                    for port in _routed_ports(candidate):
                        if port.address is not None and port.address.network == iface.address.network:
                            far, found_by = (other, port.name), "subnet"
            if far is None:
                described = LINK_DESCRIPTION.match(iface.description or "")
                if described and described.group(1) not in switches:
                    me = plan.devices[name].role
                    _placeholder(plan, described.group(1), switch.model, "spine" if me != "spine" else "leaf")
                    far, found_by = (described.group(1), described.group(2)), "description"
                elif described and any(p.name == described.group(2) for p in _routed_ports(switches[described.group(1)][0])):
                    far, found_by = (described.group(1), described.group(2)), "description"   # unnumbered, no LLDP
            if far is None:
                plan.misfit(name, f"{iface.name}: routed port with no fabric neighbour found (LLDP, subnet, description)")
                continue
            other, port = far
            seen.update({(name, iface.name), (other, port)})
            near_address = iface.address
            far_iface = switches[other][0].interfaces.get(port) if other in switches else None
            if far_iface is not None:
                far_address = far_iface.address
            else:
                far_address = other_host(near_address) if near_address is not None else None
            unnumbered = iface.unnumbered is not None
            if unnumbered and iface.unnumbered != "LoopBack0":
                plan.misfit(name, f"{iface.name}: unnumbered from {iface.unnumbered}, not LoopBack0")
                continue
            if far_iface is not None and (far_iface.unnumbered is not None) != unnumbered:
                plan.misfit(name, f"{iface.name}: unnumbered on one end only (link to {other} {port})")
                continue
            roles = (plan.devices[name].role, plan.devices[other].role)
            if roles[0] == "spine" and roles[1] != "spine":
                link = Link(name, iface.name, other, port, near_address, far_address, unnumbered, found_by,
                            tuple(cabled))
            elif roles[1] == "spine" and roles[0] != "spine":
                link = Link(other, port, name, iface.name, far_address, near_address, unnumbered, found_by,
                            tuple((b, a) for a, b in cabled))
            else:
                plan.misfit(name, f"{iface.name}: link to {other} {port} is not between a spine and a leaf")
                continue
            plan.links.append(link)
    plan.links.sort(key=lambda l: (l.spine, l.spine_port))


def _spine_loopbacks(plan: ImportPlan, switches, by_loopback: dict[str, str]) -> None:
    """A placeholder spine's LoopBack0 is the leaf's fabric BGP peer that no read switch has;
    with several such peers and spines it cannot be told which is which."""
    for name, (switch, _) in sorted(switches.items()):
        if switch.bgp is None or plan.devices[name].role == "spine":
            continue
        unknown = sorted(p for p in switch.bgp.peers if p not in by_loopback and switch.bgp.peers[p].asn == plan.asn)
        spines = sorted({l.spine for l in plan.links if l.leaf == name and plan.devices[l.spine].planned
                         and plan.devices[l.spine].loopback is None})
        if len(unknown) == 1 and len(spines) == 1:
            plan.devices[spines[0]].loopback = ipaddress.IPv4Interface(f"{unknown[0]}/32")
            by_loopback[unknown[0]] = spines[0]
        elif spines:
            plan.misfit(name, f"cannot tell which BGP peer is {', '.join(spines)}")


def _values(plan: ImportPlan, switches) -> None:
    """Values the switches use where the platform would derive another one: adopted."""
    devices = plan.overrides.setdefault("devices", {})
    for name, (switch, _) in sorted(switches.items()):
        own: dict = {}
        pair = next((p for p in plan.pairs if name in p.devices), None)
        if switch.dfs is not None and pair is not None:
            peer = next(d for d in pair.devices if d != name)
            derived = 150 if name < peer else 100
            if switch.dfs.priority not in (None, derived):
                own["dfs_priority"] = switch.dfs.priority
        if switch.nve is not None:
            if switch.nve["name"] != NVE:
                plan.misfit(name, f"{switch.nve['name']}: the platform's NVE interface is {NVE}")
            vtep = plan.devices[name].vtep
            if vtep is None or switch.nve["source"] != str(vtep.ip):
                plan.misfit(name, "NVE source is not the VTEP loopback's address")
            elif switch.nve["mac"] and pair is not None and switch.nve["mac"] != nve_mac(vtep.ip):
                own["nve_mac"] = switch.nve["mac"]
            if pair is not None and switch.nve["pip_source"]:
                peer = next(d for d in pair.devices if d != name)
                want = [plan.devices[d].pip or plan.devices[d].loopback for d in (name, peer)]
                if [switch.nve["pip_source"], switch.nve["pip_peer"]] != [str(w.ip) if w else None for w in want]:
                    plan.misfit(name, f"pip-source {switch.nve['pip_source']} peer {switch.nve['pip_peer']}: the "
                                      f"platform uses {' and '.join(str(w.ip) if w else '?' for w in want)}")
        descriptions = {}
        for link in plan.links:
            for me, port, other, other_port in ((link.spine, link.spine_port, link.leaf, link.leaf_port),
                                                (link.leaf, link.leaf_port, link.spine, link.spine_port)):
                if me == name:
                    text = switch.interfaces[port].description
                    if text != f"to {other} {other_port}":
                        descriptions[port] = text
        if pair is not None:
            peer = next(d for d in pair.devices if d != name)
            trunk = f"Eth-Trunk{pair.peer_link_trunk}"
            text = switch.interfaces[trunk].description if trunk in switch.interfaces else None
            if text != f"peer-link to {peer}":
                descriptions[trunk] = text
        links = {p for l in plan.links for p in (l.spine_port, l.leaf_port)}
        for iface in switch.interfaces.values():   # server trunks have none of the platform's own
            if iface.name.startswith("Eth-Trunk") and "." not in iface.name and not iface.peer_link \
                    and iface.name not in links and iface.description:
                descriptions[iface.name] = iface.description
        if descriptions:
            own["descriptions"] = descriptions
        for process, ospf in switch.ospf.items():
            if ospf["vpn"] is None and process != OSPF_PROCESS:
                plan.overrides.setdefault("fabric", {})["ospf_process"] = process
            if ospf["vpn"] is None and ospf["areas"] and ospf["areas"] != [OSPF_AREA]:
                plan.misfit(name, f"OSPF process {process} areas {', '.join(ospf['areas'])}: the platform uses one "
                                  f"area {OSPF_AREA}")
        if own:
            devices.setdefault(name, {}).update(own)
    if not devices:
        plan.overrides.pop("devices")


SYSTEM_VPNS = ("_management_vpn_", "_public_")


def _scheme(plan: ImportPlan, vni: int) -> dict:
    """A segment's own values (imported fabrics: generators/fabric.py segment_scheme)."""
    return plan.overrides.setdefault("segments", {}).setdefault(str(vni), {})


def _device_override(plan: ImportPlan, name: str) -> dict:
    return plan.overrides.setdefault("devices", {}).setdefault(name, {})


def _fabric_overrides(plan: ImportPlan, found: dict[str, dict[str, object]], derived: dict[str, object]) -> None:
    """Fabric-wide values the switches use where the platform would derive others (`found`: key ->
    switch -> value): adopted when every switch agrees; otherwise a misfit on each that differs."""
    for key, values in found.items():
        counts = [v for v in values.values()]
        common = max(counts, key=counts.count)
        for name, value in values.items():
            if value != common:
                plan.misfit(name, f"{key.replace('_', ' ')}: {value} here, {common} on the other switches")
        if common != derived[key]:
            plan.overrides.setdefault("fabric", {})[key] = common


def _underlay(plan: ImportPlan, switches) -> None:
    """An imported fabric's underlay forms, adopted as values (generators read them
    from the overrides document): DFS options, OSPF timers and link options, the bypass VLAN's
    name, BGP peers and route reflection, SNMP left alone, `traffic-segment enable`."""
    link_ports = {}
    for link in plan.links:
        link_ports.setdefault(link.spine, []).append(link.spine_port)
        link_ports.setdefault(link.leaf, []).append(link.leaf_port)
    by_loopback = {str(d.loopback.ip): n for n, d in plan.devices.items() if d.loopback is not None}
    found: dict[str, dict[str, object]] = {}
    derived = {"dfs_consistency_check": None, "dfs_authentication": True, "bypass_vlan_name": None,
               "ospf_bfd_all_interfaces": None, "ospf_lsa_arrival": None, "link_mtu": FABRIC_MTU,
               "link_ospf_bfd": True, "link_hold_max_cost": None, "reflect_clients": True, "snmp": True}

    def value(name: str, key: str, v) -> None:
        found.setdefault(key, {})[name] = v

    for name, (switch, _) in sorted(switches.items()):
        top = tree(switch.config)
        if switch.dfs is not None:
            check = next((n.words[-1] for g in top.find("dfs-group") for n in g.find("consistency-check enable mode")),
                         None)
            value(name, "dfs_consistency_check", check)
            value(name, "dfs_authentication", switch.dfs.keyed)
            vlan = next((n for n in top.find(f"vlan {plan.bypass_vlan}")), None) if plan.bypass_vlan else None
            if vlan is not None:
                value(name, "bypass_vlan_name", next((r.split(" ", 1)[1] for r in vlan.rows() if r.startswith("name ")),
                                                     None))
        for process in top.find(f"ospf {plan.overrides.get('fabric', {}).get('ospf_process', OSPF_PROCESS)}"):
            rows = process.rows()
            bfd = next(([int(r.split()[3]), int(r.split()[5])] for r in rows
                        if r.startswith("bfd all-interfaces min-tx-interval")), None)
            if bfd is not None and "bfd all-interfaces enable" not in rows:
                bfd = None
            value(name, "ospf_bfd_all_interfaces", bfd)
            value(name, "ospf_lsa_arrival", next(([int(w) for w in r.split()[2:]] for r in rows
                                                  if r.startswith("lsa-arrival-interval intelligent-timer")), None))
        for port in link_ports.get(name, []):
            iface = switch.interfaces.get(port)
            node = next(iter(top.find(f"interface {port}")), None)
            if iface is None or node is None:
                continue
            value(name, "link_mtu", iface.mtu)
            value(name, "link_ospf_bfd", iface.ospf_bfd)
            value(name, "link_hold_max_cost", next((int(r.split()[-1]) for r in node.rows()
                                                    if r.startswith("ospf peer hold-max-cost timer")), None))
        if switch.bgp is not None:
            peers = sorted(by_loopback[a] for a in switch.bgp.peers if a in by_loopback)
            cabled = sorted({l.leaf if l.spine == name else l.spine for l in plan.links if name in (l.spine, l.leaf)})
            if peers and peers != cabled:
                _device_override(plan, name)["evpn_peers"] = peers
            if plan.devices[name].role == "spine" and peers:
                reflects = any("reflect-client" in " ".join(rows) for p in switch.bgp.peers.values()
                               for rows in p.families.values())
                value(name, "reflect_clients", reflects)
        version = next((r.split()[3] for r in top.rows() if r.startswith("snmp-agent sys-info version ")), None)
        # the platform makes v2c only: a switch running v3 keeps its SNMP, left alone
        value(name, "snmp", version != "v3")
        if "traffic-segment enable" in top.rows():
            _device_override(plan, name)["traffic_segment_enable"] = True
    _fabric_overrides(plan, found, derived)


def choose(plan: ImportPlan, architecture: str, gateway_pair: tuple[str, str] | None) -> None:
    """The architecture, as found or as the operator says when the configs cannot tell (a
    centralized fabric without a routed segment looks symmetric)."""
    if architecture not in ("symmetric", "centralized"):
        raise ImportError_("the architecture is symmetric or centralized")
    if architecture == "symmetric":
        plan.architecture, plan.gateway_pair = "symmetric", None
        return
    pair = next((p for p in plan.pairs if set(p.devices) == set(gateway_pair or ())), None)
    if pair is None:
        raise ImportError_("a centralized fabric's gateway pair is one of its M-LAG pairs")
    plan.architecture, plan.gateway_pair = "centralized", pair.devices
    for device in pair.devices:
        if plan.devices[device].role == "leaf":
            plan.devices[device].role = "border-leaf"


def _pair_of(plan: ImportPlan, name: str) -> Pair | None:
    return next((p for p in plan.pairs if name in p.devices), None)


def _tenants(plan: ImportPlan, switches, chosen: bool = False, drop=()) -> None:
    """Segments (bridge domains), their ports (L2 sub-interfaces), routing (VBDIF gateways,
    segment VPN instances, peering, firewall default routes), services and single trunks."""
    asn = plan.asn
    # -- segments: bridge domain <-> VNI, the same on every switch
    # A bridge domain with a VBDIF in the management VPN carries the management network:
    # management plane, never a segment. Each switch's part of it is
    # recorded (overrides, generators/fabric.py management) and every generator leaves it alone.
    left_out = {int(i.name.removeprefix("Vbdif")) for s, _ in switches.values() for i in s.interfaces.values()
                  if i.name.startswith("Vbdif") and i.vpn == MANAGEMENT_VPN}
    for name, (switch, _) in sorted(switches.items()):
        for bd in switch.bridge_domains.values():
            if bd.id in left_out or bd.id in drop:
                continue
            if bd.vni is None:
                plan.misfit(name, f"bridge-domain {bd.id} has no VXLAN VNI")
                continue
            known = plan.segments.get(bd.vni)
            if known is not None and known.bd != bd.id:
                plan.misfit(name, f"VNI {bd.vni} is bridge-domain {bd.id} here, {known.bd} elsewhere")
                continue
            plan.segments.setdefault(bd.vni, Segment(bd.vni, bd.id, f"bd{bd.id}"))
            if bd.rd != "auto":
                plan.misfit(name, f"bridge-domain {bd.id}: EVPN route-distinguisher {bd.rd} (the platform uses auto)")
            l2 = f"{asn}:{bd.vni}"
            if set(bd.import_rts) != {l2} and len(bd.import_rts) == 1 and bd.import_rts[0] in bd.export_rts:
                # an imported fabric's own L2 route target: adopted for the segment, extra exports left to the patch
                scheme = _scheme(plan, bd.vni)
                if scheme.setdefault("l2_rt", bd.import_rts[0]) != bd.import_rts[0]:
                    plan.misfit(name, f"bridge-domain {bd.id}: L2 route target {bd.import_rts[0]}, elsewhere "
                                      f"{scheme['l2_rt']}")
            elif l2 not in bd.export_rts or l2 not in bd.import_rts:
                plan.misfit(name, f"bridge-domain {bd.id}: L2 route target is not {l2}")
            elif set(bd.import_rts) != {l2}:
                plan.misfit(name, f"bridge-domain {bd.id}: imports route targets other than {l2}")
            if set(bd.arp) == {"arp l2-proxy enable", "arp collect host enable"} \
                    and f"Vbdif{bd.id}" not in switch.interfaces:
                _scheme(plan, bd.vni)["bd_arp"] = True   # an imported fabric's layer-2 leaves; the patch shows any without it
            if switch.nve is not None and bd.vni not in switch.nve["vnis"]:
                plan.misfit(name, f"bridge-domain {bd.id}: VNI {bd.vni} not on {switch.nve['name']}")
    by_bd = {seg.bd: seg for seg in plan.segments.values()}
    not_services: set[str] = set()   # VPN instances of management or dropped bridge domains
    for bd in sorted(left_out):
        for name, (switch, _) in sorted(switches.items()):
            here = switch.bridge_domains.get(bd)
            vbdif = switch.interfaces.get(f"Vbdif{bd}")
            if here is None and vbdif is None:
                continue
            record = plan.overrides.setdefault("devices", {}).setdefault(name, {}).setdefault("management", {})
            parts = {"bridge_domains": [bd],
                     "vnis": [here.vni] if here is not None and here.vni is not None else [],
                     "vpn_instances": [vbdif.vpn] if vbdif is not None and vbdif.vpn not in (None, *SYSTEM_VPNS) else [],
                     "vlans": list(here.vlans) if here is not None else [],
                     "interfaces": [i.name for i in switch.interfaces.values() if i.l2 and i.bridge_domain == bd]}
            for kind, values in parts.items():
                if values:
                    record[kind] = sorted(set(record.get(kind, [])) | set(values))
            not_services.update(parts["vpn_instances"])
    # Bridge domains the operator leaves out (an imported fabric's TEST segments): not
    # imported, so the first deploy would remove them with their gateways and VPN instances.
    for bd in sorted(set(drop)):
        left_out.add(bd)
        not_services.update(vbdif.vpn for s, _ in switches.values()
                            if (vbdif := s.interfaces.get(f"Vbdif{bd}")) is not None and vbdif.vpn)

    if asn is not None and asn > 65535:   # route targets <ASN>:<VNI> leave 2 bytes for the VNI
        for segment in sorted((s for s in plan.segments.values() if s.vni > 65535
                               and "l2_rt" not in _scheme(plan, s.vni)), key=lambda s: s.vni):
            plan.misfit("fabric", f"bridge-domain {segment.bd}: VNI {segment.vni} is above 65535, which a fabric "
                                  f"with the 4-byte ASN {asn} cannot carry: left out")
            del plan.segments[segment.vni]
            del by_bd[segment.bd]
            left_out.add(segment.bd)   # and its gateways, VPN instance and ports with it

    # -- segment VPN instances and gateways
    vbdif_on: dict[int, set[str]] = {}
    macs: dict[int, str] = {}
    for name, (switch, _) in sorted(switches.items()):
        for iface in switch.interfaces.values():
            if not iface.name.startswith("Vbdif"):
                continue
            bd = int(iface.name.removeprefix("Vbdif"))
            if bd in left_out:
                continue
            segment = by_bd.get(bd)
            if segment is None or iface.vpn is None or iface.address is None:
                plan.misfit(name, f"{iface.name}: no bridge domain, VPN instance or address")
                continue
            vbdif_on.setdefault(bd, set()).add(name)
            if segment.vrf not in (None, iface.vpn):
                plan.misfit(name, f"{iface.name} is in {iface.vpn}, elsewhere in {segment.vrf}")
            segment.vrf = iface.vpn
            if segment.gateway not in (None, iface.address):
                plan.misfit(name, f"{iface.name}: gateway {iface.address} differs from {segment.gateway}")
            segment.gateway = segment.gateway or iface.address
            scheme = _scheme(plan, segment.vni)   # an imported fabric's gateway forms, per segment
            if not iface.anycast_gateway:
                scheme["anycast"] = False
            if "arp broadcast-detect enable" in iface.other:
                scheme["arp_broadcast_detect"] = True
            if iface.arp_collect:
                scheme["arp_collect_host"] = True   # only read where the fabric is centralized
            if iface.mac:
                macs.setdefault(segment.vni, iface.mac)
    for name, (switch, _) in sorted(switches.items()):
        for vpn in switch.vpn_instances.values():
            if vpn.name in SYSTEM_VPNS:
                continue
            # A segment's instance is <segment name>-<bd_id>: the import takes the name from it.
            match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*)-(\d+)", vpn.name)
            segment = by_bd.get(int(match.group(2))) if match else None
            if segment is None:
                segment = next((s for s in plan.segments.values() if s.vrf == vpn.name), None)
            if segment is None and match and vpn.l3_vni and vpn.l3_vni > 1_000_000 \
                    and int(match.group(2)) not in left_out:
                # a routed segment attached nowhere: its VPN instance is all there is (L3 VNI = VNI + 1,000,000)
                segment = plan.segments[vpn.l3_vni - 1_000_000] = Segment(vpn.l3_vni - 1_000_000, int(match.group(2)),
                                                                          f"bd{match.group(2)}")
                by_bd[segment.bd] = segment
            if segment is None:
                continue   # a service's, or not the platform's: services below
            segment.vrf = segment.vrf or vpn.name
    for segment in plan.segments.values():
        named = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*)-(\d+)", segment.vrf or "")
        if named and int(named.group(2)) == segment.bd:
            segment.name = named.group(1)
        elif segment.vrf and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", segment.vrf):
            segment.name = segment.vrf   # an imported fabric's OOB-HOSTS...: the instance keeps its name
    names = [s.name for s in plan.segments.values()]
    for segment in plan.segments.values():
        if names.count(segment.name) > 1:   # segment names are unique in a fabric
            segment.name = f"bd{segment.bd}"
        if segment.vrf is not None and segment.vrf != segment_vpn(segment.name, segment.bd):
            _scheme(plan, segment.vni)["vrf"] = segment.vrf
    # the fabric's gateway MAC is its segments' most common one; the others are the segment's own
    if macs:
        plan.gateway_mac = Counter(macs.values()).most_common(1)[0][0]
        for vni, mac in macs.items():
            if mac != plan.gateway_mac:
                _scheme(plan, vni)["gateway_mac"] = mac

    # architecture: gateways on one M-LAG pair, and no L3 VNI anywhere, is centralized
    # (an M-LAG spine pair exists only in a centralized fabric, where it may be the gateway pair)
    holders = set().union(*vbdif_on.values()) if vbdif_on else set()
    l3 = any(v.l3_vni for s, _ in switches.values() for v in s.vpn_instances.values() if is_segment_vpn(v.name))
    pair = _pair_of(plan, next(iter(holders))) if holders else None
    spine_pairs = [p for p in plan.pairs if plan.devices[p.devices[0]].role == "spine"]
    if chosen:
        pass
    elif holders and not l3 and pair is not None and holders <= set(pair.devices):
        choose(plan, "centralized", pair.devices)
    elif not holders and len(spine_pairs) == 1:
        choose(plan, "centralized", spine_pairs[0].devices)
    vrf_of = {s.vrf: s for s in plan.segments.values() if s.vrf}
    imports: dict[int, set[str]] = {}
    defaults: dict[int, set[tuple[str, str | None]]] = {}   # segment -> its default routes (service, next hop)

    for name, (switch, _) in sorted(switches.items()):
        for vpn in switch.vpn_instances.values():
            segment = vrf_of.get(vpn.name)
            if segment is None:
                continue
            loopback = plan.devices[name].loopback
            scheme = _scheme(plan, segment.vni)
            if loopback is not None and vpn.rd != f"{loopback.ip}:{segment.bd}":
                # an imported fabric's one RD per segment: adopted when every switch uses the same
                if scheme.setdefault("rd", vpn.rd) != vpn.rd:
                    plan.misfit(name, f"{vpn.name}: route distinguisher {vpn.rd}, elsewhere {scheme['rd']} (the "
                                      f"platform uses {loopback.ip}:{segment.bd})")
            if vpn.l3_vni is None and not vpn.export_evpn and not vpn.import_evpn:
                if plan.architecture == "symmetric":
                    plan.misfit(name, f"{vpn.name}: no L3 VNI (a symmetric fabric's segments have one)")
                continue
            if vpn.l3_vni is None:
                plan.misfit(name, f"{vpn.name}: EVPN route targets without an L3 VNI")
                continue
            if vpn.l3_vni != segment.vni + 1_000_000 or plan.architecture == "centralized":
                # an L3 VNI of its own, or one in a centralized fabric (an imported fabric's gateways)
                if scheme.setdefault("l3_vni", vpn.l3_vni) != vpn.l3_vni:
                    plan.misfit(name, f"{vpn.name}: L3 VNI {vpn.l3_vni}, elsewhere {scheme['l3_vni']}")
            derived = f"{asn}:{scheme.get('l3_vni') or segment.vni + 1_000_000}"
            both = [rt for rt in vpn.export_evpn if rt in vpn.import_evpn]
            if derived in both:
                own = derived
            elif len(both) == 1:
                own = scheme.setdefault("l3_rt", both[0])
                if own != both[0]:
                    plan.misfit(name, f"{vpn.name}: L3 route target {both[0]}, elsewhere {own}")
            else:
                plan.misfit(name, f"{vpn.name}: L3 route target is not {derived}")
                continue
            imports.setdefault(segment.vni, set()).update(rt for rt in vpn.import_evpn if rt != own)
        for route in switch.static_routes:
            source, target = vrf_of.get(route.vpn), route.target_vpn
            if source is not None and target in vrf_of and route.next_hop is None:
                peer = vrf_of[target]
                if route.prefix == (peer.gateway.network if peer.gateway else None):
                    source.peers.add(peer.vni)   # centralized peering: inter-VPN routes
            elif source is not None and route.prefix.prefixlen == 0 and target is not None:
                source.firewall = target
                defaults.setdefault(source.vni, set()).add((target, route.next_hop))
            elif source is None and target in vrf_of and route.vpn is not None:
                peer = vrf_of[target]   # the service's route back to a segment: its subnet
                if peer.gateway is None:
                    peer.gateway = ipaddress.IPv4Interface(f"{next(route.prefix.hosts())}/{route.prefix.prefixlen}")
    # explicit peering: a segment importing another's L3 route target (once every segment's is known)
    l3_rts = {(_scheme(plan, s.vni).get("l3_rt")
               or f"{asn}:{_scheme(plan, s.vni).get('l3_vni') or s.vni + 1_000_000}"): s for s in plan.segments.values()}
    for vni, rts in imports.items():
        plan.segments[vni].peers.update(l3_rts[rt].vni for rt in rts if rt in l3_rts and l3_rts[rt].vni != vni)
    for segment in plan.segments.values():
        if segment.vrf is not None and segment.gateway is None:
            plan.misfit("fabric", f"segment VNI {segment.vni} is routed but its gateway subnet was not found")
        if segment.gateway is not None and segment.gateway.ip != next(segment.gateway.network.hosts()):
            plan.misfit("fabric", f"segment VNI {segment.vni}: gateway {segment.gateway} is not the subnet's first host")

    # -- services: a VPN instance of the platform's kind with a Vlanif on a trunk port
    own_mlag: dict[str, dict[str, str]] = {}   # service -> each switch's M-LAG address
    for name, (switch, _) in sorted(switches.items()):
        for vpn in switch.vpn_instances.values():
            if vpn.name in SYSTEM_VPNS or vpn.name in vrf_of or vpn.name in not_services:
                continue
            vlanifs = [i for i in switch.interfaces.values() if i.name.startswith("Vlanif") and i.vpn == vpn.name]
            if not any(i.vpn == vpn.name for i in switch.interfaces.values()):
                # bound to no interface: kept as it is
                _device_override(plan, name).setdefault("kept_vpns", []).append(vpn.name)
                continue
            if len(vlanifs) != 1:
                plan.misfit(name, f"VPN instance {vpn.name}: not a segment's, and not a service (one Vlanif)")
                continue
            vlanif = vlanifs[0]
            vlan = int(vlanif.name.removeprefix("Vlanif"))
            ports = [i.name for i in switch.interfaces.values() if vlan in i.allow_vlans and i.link_type == "trunk"]
            # one dedicated port, or none of its own: the VLAN rides VLAN trunks (an imported fabric's firewall contexts)
            port = ports[0] if len(ports) == 1 else None
            scheme = plan.overrides.setdefault("services", {}).setdefault(vpn.name, {})
            loopback = plan.devices[name].loopback
            if loopback is not None and vpn.rd != f"{loopback.ip}:{vlan}":
                if scheme.setdefault("rd", vpn.rd) != vpn.rd:   # one RD on every switch
                    plan.misfit(name, f"{vpn.name}: route distinguisher {vpn.rd}, elsewhere {scheme['rd']} "
                                      f"(the platform uses {loopback.ip}:{vlan})")
            if "arp proxy enable" in vlanif.other:
                scheme["arp_proxy"] = True
            if vlanif.mac:
                scheme.setdefault("mac", vlanif.mac)   # dropped below where it is the derived one
            pair = _pair_of(plan, name)
            if vlanif.mlag_address is not None and pair is not None:
                devices, address = pair.devices, vlanif.mlag_address
            else:
                devices, address = (name,), vlanif.address
            service = plan.services.get(vpn.name)
            if service is None:
                service = plan.services[vpn.name] = Service(vpn.name, devices, port, vlan, address, None, None,
                                                            "isp")
            if vlanif.mlag_address is not None:
                service.addresses[name] = str(vlanif.address)
                own_mlag.setdefault(vpn.name, {})[name] = str(vlanif.mlag_address)
            for route in switch.static_routes:
                if route.target_vpn == vpn.name and route.prefix.prefixlen == 0 and route.next_hop:
                    service.next_hop, service.kind = route.next_hop, "firewall"
            peers = switch.bgp.vpn_peers.get(vpn.name, {}) if switch.bgp else {}
            family = switch.bgp.vpn_families.get(vpn.name, []) if switch.bgp else []
            for peer in peers.values():
                if peer.bfd and "import-route static" in family:   # the firewall's eBGP (FirewallBgp)
                    service.next_hop, service.asn, service.kind = peer.address, peer.asn, "firewall"
                elif peer.connect_interface == vlanif.name and "import-route static" in family:
                    # an imported fabric's iBGP to its firewall contexts over their Vlanif, without BFD
                    service.next_hop, service.asn, service.kind = peer.address, peer.asn, "firewall"
                    scheme["connect_interface"] = True
                    if not peer.bfd:
                        scheme["bfd"] = False
    for name, mlag in own_mlag.items():
        service = plan.services[name]
        if len(set(service.addresses.values())) == 1 and len(set(mlag.values())) == len(mlag) == 2:
            # imported fabrics: the shared address is the main one, each switch's own the M-LAG one
            service.address, service.addresses = ipaddress.IPv4Interface(next(iter(service.addresses.values()))), mlag
            plan.overrides["services"][name]["shared_main"] = True
    for service in plan.services.values():
        scheme = plan.overrides["services"][service.name]
        if not service.addresses or scheme.get("mac") == mlag_mac(service.address.ip):
            scheme.pop("mac", None)
    for service in plan.services.values():
        if service.next_hop is None and service.address.network.prefixlen == 30 and not service.addresses:
            service.next_hop = str(other_host(service.address).ip)
        if service.next_hop is None:
            plan.misfit("fabric", f"service {service.name}: its next hop (firewall or ISP router) was not found")
    service_ports = {(d, s.port) for s in plan.services.values() for d in s.devices}
    for segment in plan.segments.values():
        found = defaults.get(segment.vni, set())
        service = plan.services.get(segment.firewall) if segment.firewall else None
        if service is not None and service.next_hop is not None and found != {(service.name, service.next_hop)}:
            plan.misfit("fabric", f"segment VNI {segment.vni}: default routes "
                                  f"{', '.join(f'via {t} {h}' for t, h in sorted(found, key=str))} (the platform "
                                  f"makes one, via {service.name} {service.next_hop}): imported without them")
            segment.firewall = None
            continue
        if segment.firewall is not None and (plan.services.get(segment.firewall) is None
                                             or plan.services[segment.firewall].next_hop is None):
            plan.misfit("fabric", f"segment VNI {segment.vni}: its default route goes to {segment.firewall}, "
                                  "which is not a service the platform can make: imported without it")
            segment.firewall = None

    # -- ports: L2 sub-interfaces carry segments; single trunks
    untagged: list[tuple[str, str, int, int]] = []
    for name, (switch, _) in sorted(switches.items()):
        for iface in switch.interfaces.values():
            if iface.l2:
                port, number = iface.name.rsplit(".", 1)
                segment = by_bd.get(iface.bridge_domain)
                if iface.bridge_domain in left_out:
                    continue   # its segment's misfit says why
                if segment is None:
                    plan.misfit(name, f"{iface.name}: bridge domain {iface.bridge_domain} has no VNI")
                    continue
                vid = iface.vid if iface.encapsulation == "dot1q" else None
                if iface.encapsulation not in ("dot1q", "untag"):
                    plan.misfit(name, f"{iface.name}: encapsulation the platform does not make")
                    continue
                if vid is None:
                    untagged.append((name, iface.name, int(number), segment.bd))
                elif int(number) != segment.bd:
                    plan.misfit(name, f"{iface.name}: the platform numbers it {port}.{segment.bd}")
                plan.attachments.append((name, port, segment.vni, vid))
            elif iface.name.startswith("Eth-Trunk") and "." not in iface.name and not iface.peer_link \
                    and iface.mlag is None:
                number = int(iface.name.removeprefix("Eth-Trunk"))
                members = tuple(sorted(i.name for i in switch.interfaces.values() if i.trunk == number))
                plan.trunks.append((name, number, members))
                if iface.mode is None:   # manual (an imported fabric's link trunks): kept without `mode lacp-static`
                    _device_override(plan, name).setdefault("manual_trunks", []).append(iface.name)
            elif (PORT.fullmatch(iface.name) or iface.name.startswith("Eth-Trunk")) and iface.link_type \
                    and (name, iface.name) not in service_ports:
                lone = iface.name.startswith("Eth-Trunk") and not any(   # left out: no members
                    i.trunk == int(iface.name.removeprefix("Eth-Trunk")) for i in switch.interfaces.values())
                if lone:
                    continue   # its M-LAG misfit says why
                if plan.devices[name].role == "leaf" and iface.link_type == "trunk":
                    plan.misfit(name, f"{iface.name}: a VLAN trunk on a leaf: the platform attaches segments with "
                                      "L2 sub-interfaces (VLAN trunks are kept only on border switches, for services)")
                elif iface.link_type == "trunk" and iface.allow_vlans and "." not in iface.name:
                    # kept as the switch has it on a border switch, for services: never segments
                    _device_override(plan, name).setdefault("vlan_trunks", {})[iface.name] = {
                        "vlans": vlan_list(iface.allow_vlans),
                        **({"undo_vlan1": True} if "undo port trunk allow-pass vlan 1" in iface.other else {})}
                else:
                    plan.misfit(name, f"{iface.name}: VLAN-based port (link-type {iface.link_type}): the platform "
                                      "attaches segments with L2 sub-interfaces")
    for pair in plan.pairs:   # an M-LAG trunk carries the same VLANs on both sides: their union
        sides = [plan.overrides.get("devices", {}).get(d, {}).get("vlan_trunks") or {} for d in pair.devices]
        for trunk in sorted(sides[0].keys() & sides[1].keys()):
            if sides[0][trunk] != sides[1][trunk]:
                vlans = [w for side in sides for w in [side[trunk]["vlans"]]]
                union = vlan_list(sorted({v for text in vlans for v in vlan_numbers(text.split())}))
                plan.misfit("fabric", f"{trunk} of {' and '.join(pair.devices)}: VLANs {vlans[0]} and {vlans[1]}; "
                                      f"both get {union} at the first deploy")
                for side in sides:
                    side[trunk] = {**side[trunk], "vlans": union,
                                   **({"undo_vlan1": True} if any(s[trunk].get("undo_vlan1") for s in sides) else {})}
    # what each switch has beyond its ports' needs (imported fabrics): bridge domains with no port, Nve
    # VNIs with no bridge domain, VLANs nothing uses; kept as they are
    attached = {(d, vni) for d, _, vni, _ in plan.attachments}
    for name, (switch, _) in sorted(switches.items()):
        gateway = plan.architecture == "symmetric" or name in (plan.gateway_pair or ())
        bare = sorted(by_bd[bd].vni for bd in switch.bridge_domains if bd in by_bd and bd not in left_out
                      and (name, by_bd[bd].vni) not in attached and not (gateway and by_bd[bd].gateway is not None))
        vnis = {bd.vni for bd in switch.bridge_domains.values()}
        loose = sorted(v for v in (switch.nve or {}).get("vnis", ()) if v not in vnis)
        used = {s.vlan for s in plan.services.values() if name in s.devices} | {plan.bypass_vlan} \
            | set((plan.overrides.get("devices", {}).get(name, {}).get("management") or {}).get("vlans") or [])
        unused = sorted(v for v in switch.vlans if v not in used)
        for key, values in (("segments_without_ports", bare), ("kept_vnis", loose), ("kept_vlans", unused)):
            if values:
                _device_override(plan, name)[key] = values
    # untagged sub-interfaces are <port>.1, or <port>.<bd_id> where every one is (imported fabrics)
    by_bd_number = bool(untagged) and all(n == bd for _, _, n, bd in untagged) and any(bd != 1 for *_, bd in untagged)
    if by_bd_number:
        plan.overrides.setdefault("fabric", {})["untagged_subif"] = "bd"
    for name, iface, number, bd in untagged:
        if number != (bd if by_bd_number else 1):
            plan.misfit(name, f"{iface}: the platform numbers it {iface.rsplit('.', 1)[0]}.{bd if by_bd_number else 1}")
    for vni, scheme in list(plan.overrides.get("segments", {}).items()):
        if not scheme or int(vni) not in plan.segments:
            del plan.overrides["segments"][vni]
    if plan.overrides.get("segments") == {}:
        del plan.overrides["segments"]
    for name, scheme in list(plan.overrides.get("services", {}).items()):
        if not scheme or name not in plan.services:
            del plan.overrides["services"][name]
    if plan.overrides.get("services") == {}:
        del plan.overrides["services"]


def _pools(plan: ImportPlan) -> None:
    def hosts(values):
        return [ipaddress.IPv4Network(f"{v.ip}/32") for v in values if v is not None]

    plan.pools = {
        "loopback": covering(hosts(d.loopback for d in plan.devices.values())),
        "vtep": covering(hosts(d.vtep for d in plan.devices.values())),
        "p2p": covering([l.network for l in plan.links if l.network is not None]),
        "mlag_bypass": covering([a.network for p in plan.pairs for a in p.bypass.values()]),
        "gateways": covering([s.gateway.network for s in plan.segments.values() if s.gateway], at_most=16),
    }
    if pips := hosts(d.pip for d in plan.devices.values()):   # the PIP layout only
        plan.pools["pip"] = covering(pips)
