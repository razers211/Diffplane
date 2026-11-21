"""What one switch's running config says, for importing an existing fabric.

Input: the outputs of `orchestrator/ssh_read.py` (`display current-configuration`, `display
version`, `display lldp neighbor brief`). Output: the facts the platform models, as the
switch has them (names and numbers included, so values the platform would derive
differently can be adopted), plus every line the importer did not
understand. The config holds secrets: nothing here keeps or returns a secret line (DFS key,
SNMP community, AAA), only that one is present.

Forms are those of a test switch (V300R023, probe) and of our own generators; the
row format of `display lldp neighbor brief` with neighbours is unverified (the lab has none).
"""

import ipaddress
import re
from dataclasses import dataclass, field

MANAGEMENT_VPN = "_management_vpn_"
PORT = re.compile(r"\d*GE\d+/\d+/\d+")
SUBIF = re.compile(r"(\d*GE\d+/\d+/\d+|Eth-Trunk\d+)\.(\d+)")


class ImportParseError(ValueError):
    pass


# -- the config as a tree ---------------------------------------------------------------------

@dataclass
class Node:
    row: str
    children: list["Node"] = field(default_factory=list)

    @property
    def words(self) -> list[str]:
        return self.row.split()

    def rows(self) -> list[str]:
        return [c.row for c in self.children]

    def find(self, prefix: str) -> list["Node"]:
        return [c for c in self.children if c.row == prefix or c.row.startswith(prefix + " ")]


def tree(text: str) -> Node:
    """VRP's indentation: one space per level; `#` separates sections, `!` lines are comments."""
    root = Node("")
    stack: list[tuple[int, Node]] = [(-1, root)]
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip(" ")
        if not stripped or stripped.startswith("!") or stripped == "#" or stripped == "return":
            continue
        depth = len(line) - len(stripped)
        while stack[-1][0] >= depth:
            stack.pop()
        node = Node(" ".join(stripped.split()))
        stack[-1][1].children.append(node)
        stack.append((depth, node))
    return root


def mask_length(mask: str) -> int:
    return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen


def address(ip: str, mask: str) -> ipaddress.IPv4Interface:
    return ipaddress.IPv4Interface(f"{ip}/{mask_length(mask)}")


# -- facts ------------------------------------------------------------------------------------

@dataclass
class Interface:
    name: str
    description: str | None = None
    address: ipaddress.IPv4Interface | None = None
    unnumbered: str | None = None          # `ip address unnumbered interface <name>`
    vpn: str | None = None                 # `ip binding vpn-instance`
    mtu: int | None = None
    routed: bool = False                   # `undo portswitch`
    trunk: int | None = None               # member of Eth-Trunk<n>
    ospf: tuple[int, str] | None = None    # (process, area)
    ospf_p2p: bool = False
    ospf_bfd: bool = False
    mode: str | None = None                # Eth-Trunk `mode`
    peer_link: bool = False
    mlag: int | None = None                # `dfs-group <g> m-lag <id>`
    link_type: str | None = None           # `port link-type`
    allow_vlans: tuple[int, ...] = ()
    mac: str | None = None
    mlag_address: ipaddress.IPv4Interface | None = None
    bypass: bool = False                   # `reserved for vxlan bypass`
    anycast_gateway: bool = False
    arp_collect: bool = False
    # an L2 sub-interface (`interface <port>.<n> mode l2`)
    l2: bool = False
    encapsulation: str | None = None       # "untag" or "dot1q"
    vid: int | None = None
    bridge_domain: int | None = None
    other: list[str] = field(default_factory=list)


@dataclass
class BridgeDomain:
    id: int
    vni: int | None = None
    rd: str | None = None
    export_rts: tuple[str, ...] = ()
    import_rts: tuple[str, ...] = ()
    vlans: tuple[int, ...] = ()   # `l2 binding vlan <n>`
    arp: tuple[str, ...] = ()     # `arp l2-proxy enable`, `arp collect host enable` (an imported fabric's leaves)


@dataclass
class VpnInstance:
    name: str
    rd: str | None = None
    export_evpn: tuple[str, ...] = ()
    import_evpn: tuple[str, ...] = ()
    l3_vni: int | None = None
    other: list[str] = field(default_factory=list)


@dataclass
class BgpPeer:
    address: str
    asn: int | None = None
    connect_interface: str | None = None
    bfd: bool = False
    families: dict[str, list[str]] = field(default_factory=dict)   # family -> its rows about this peer


@dataclass
class Bgp:
    asn: int
    router_id: str | None = None
    peers: dict[str, BgpPeer] = field(default_factory=dict)          # global peers
    evpn_policy_vpn_target: bool = True                              # False: `undo policy vpn-target`
    vpn_peers: dict[str, dict[str, BgpPeer]] = field(default_factory=dict)   # VPN instance -> peers
    vpn_families: dict[str, list[str]] = field(default_factory=dict)         # VPN instance -> family rows


@dataclass
class Dfs:
    group: int
    source: str | None = None
    peer: str | None = None
    vpn: str | None = None                 # heartbeat in a VPN instance (e.g. over MEth)
    priority: int | None = None
    keyed: bool = False


@dataclass
class StaticRoute:
    vpn: str | None
    prefix: ipaddress.IPv4Network
    target_vpn: str | None
    next_hop: str | None
    preference: int | None
    row: str


@dataclass
class Switch:
    name: str
    model: str | None
    version: str | None
    interfaces: dict[str, Interface]
    bridge_domains: dict[int, BridgeDomain]
    vpn_instances: dict[str, VpnInstance]
    nve: dict | None                       # {"name", "source", "pip_source", "pip_peer", "mac", "vnis"}
    dfs: Dfs | None
    bgp: Bgp | None
    ospf: dict[int, dict]                  # process -> {"router_id", "vpn", "areas"}
    static_routes: list[StaticRoute]
    vlans: tuple[int, ...]
    vlan_descriptions: dict[int, str]
    v_stp: bool
    evpn_overlay: bool
    ntp_servers: tuple[str, ...]
    ntp_vpn: str | None
    snmp: dict                             # {"location", "contact", "community": bool}
    lldp: list[tuple[str, str, str]]       # (local port, neighbour port, neighbour name)
    config: str = field(repr=False, default="")   # kept in memory for routing import; never returned

    def loopback(self, name: str) -> ipaddress.IPv4Interface | None:
        iface = self.interfaces.get(name)
        return iface.address if iface else None


def vlan_numbers(words: list[str]) -> tuple[int, ...]:
    """`3005 to 3006 4093` -> (3005, 3006, 4093)."""
    found, i = [], 0
    while i < len(words):
        if i + 2 < len(words) and words[i + 1] == "to":
            found += range(int(words[i]), int(words[i + 2]) + 1)
            i += 3
        else:
            found.append(int(words[i]))
            i += 1
    return tuple(sorted(set(found)))


def _interface(node: Node) -> Interface:
    w = node.words
    iface = Interface(w[1])
    if len(w) > 2 and w[2:] == ["mode", "l2"]:
        iface.l2 = True
    for child in node.children:
        c, row = child.words, child.row
        if c[0] == "description":
            iface.description = row.split(" ", 1)[1]
        elif c[:2] == ["ip", "address"] and len(c) >= 4 and c[2] == "unnumbered":
            iface.unnumbered = c[-1]
        elif c[:2] == ["ip", "address"] and len(c) == 4:
            iface.address = address(c[2], c[3])
        elif c[:3] == ["ip", "binding", "vpn-instance"]:
            iface.vpn = c[3]
        elif c[0] == "mtu":
            iface.mtu = int(c[1])
        elif row == "undo portswitch":
            iface.routed = True
        elif c[0] == "eth-trunk":
            iface.trunk = int(c[1])
        elif c[:2] == ["ospf", "enable"]:
            iface.ospf = (int(c[2]), c[4])
        elif row == "ospf network-type p2p":
            iface.ospf_p2p = True
        elif row == "ospf bfd enable":
            iface.ospf_bfd = True
        elif c[0] == "mode" and not iface.l2:
            iface.mode = c[1]
        elif c[0] == "peer-link":
            iface.peer_link = True
        elif c[0] == "dfs-group" and "m-lag" in c:
            iface.mlag = int(c[3])
        elif c[:2] == ["port", "link-type"]:
            iface.link_type = c[2]
        elif c[:4] == ["port", "trunk", "allow-pass", "vlan"]:
            iface.allow_vlans = vlan_numbers(c[4:])
        elif c[0] == "mac-address":
            iface.mac = c[1]
        elif c[:3] == ["m-lag", "ip", "address"]:
            iface.mlag_address = address(c[3], c[4])
        elif row == "reserved for vxlan bypass":
            iface.bypass = True
        elif row == "vxlan anycast-gateway enable":
            iface.anycast_gateway = True
        elif row == "arp collect host enable":
            iface.arp_collect = True
        elif row == "encapsulation untag":
            iface.encapsulation = "untag"
        elif c[:3] == ["encapsulation", "dot1q", "vid"]:
            iface.encapsulation, iface.vid = "dot1q", int(c[3])
        elif c[0] == "bridge-domain":
            iface.bridge_domain = int(c[1])
        elif c[:2] == ["device", "transceiver"] or row in ("port vlan exclude 1", "shutdown"):
            continue   # hardware detail or a default the platform writes itself
        else:
            iface.other.append(row)
    return iface


def _bridge_domain(node: Node) -> BridgeDomain:
    bd = BridgeDomain(int(node.words[1]))
    for child in node.children:
        c = child.words
        if c[:2] == ["vxlan", "vni"]:
            bd.vni = int(c[2])
        elif c[:3] == ["l2", "binding", "vlan"] and c[3].isdigit():
            bd.vlans += (int(c[3]),)
        elif child.row in ("arp l2-proxy enable", "arp collect host enable"):
            bd.arp += (child.row,)
        elif c[0] == "evpn":
            for row in child.children:
                r = row.words
                if r[0] == "route-distinguisher":
                    bd.rd = r[1]
                elif r[0] == "vpn-target" and r[-1] == "export-extcommunity":
                    bd.export_rts += (r[1],)
                elif r[0] == "vpn-target" and r[-1] == "import-extcommunity":
                    bd.import_rts += (r[1],)
    return bd


def _vpn_instance(node: Node) -> VpnInstance:
    vpn = VpnInstance(node.words[2])
    for child in node.children:
        c = child.words
        if c[:2] == ["vxlan", "vni"]:
            vpn.l3_vni = int(c[2])
        elif c[0] == "ipv4-family":
            for row in child.children:
                r = row.words
                if r[0] == "route-distinguisher":
                    vpn.rd = r[1]
                elif r[0] == "vpn-target" and r[2:] == ["export-extcommunity", "evpn"]:
                    vpn.export_evpn += (r[1],)
                elif r[0] == "vpn-target" and r[2:] == ["import-extcommunity", "evpn"]:
                    vpn.import_evpn += (r[1],)
                else:
                    vpn.other.append(row.row)
        else:
            vpn.other.append(child.row)
    return vpn


def _peer(peers: dict[str, BgpPeer], ip: str) -> BgpPeer:
    return peers.setdefault(ip, BgpPeer(ip))


def _peer_rows(peers: dict[str, BgpPeer], node: Node) -> list[str]:
    """Peer declarations in a BGP view; returns the rows that are not about a peer."""
    rest = []
    for child in node.children:
        c = child.words
        if c[0] == "peer" and len(c) >= 3:
            peer = _peer(peers, c[1])
            if c[2] == "as-number":
                peer.asn = int(c[3])
            elif c[2] == "connect-interface":
                peer.connect_interface = c[3]
            elif c[2:] == ["bfd", "enable"]:
                peer.bfd = True
            else:
                peer.families.setdefault("", []).append(" ".join(c[2:]))
        else:
            rest.append(child.row)
    return rest


def _family_rows(peers: dict[str, BgpPeer], family: str, node: Node) -> list[str]:
    rest = []
    for child in node.children:
        c = child.words
        if c[0] == "peer" and len(c) >= 3:
            _peer(peers, c[1]).families.setdefault(family, []).append(" ".join(c[2:]))
        else:
            rest.append(child.row)
    return rest


def _bgp(node: Node) -> Bgp:
    bgp = Bgp(int(node.words[1]))
    for child in node.children:
        c = child.words
        if c[0] == "router-id":
            bgp.router_id = c[1]
        elif c[0] == "peer":
            _peer_rows(bgp.peers, Node("", [child]))
        elif c[0] == "vpn-instance":
            _peer_rows(bgp.vpn_peers.setdefault(c[1], {}), child)
        elif c[:2] == ["ipv4-family", "unicast"]:
            _family_rows(bgp.peers, "ipv4", child)
        elif c[0] == "l2vpn-family" and c[1] == "evpn":
            for row in _family_rows(bgp.peers, "evpn", child):
                if row == "undo policy vpn-target":
                    bgp.evpn_policy_vpn_target = False
        elif c[:2] == ["ipv4-family", "vpn-instance"]:
            peers = bgp.vpn_peers.setdefault(c[2], {})
            bgp.vpn_families[c[2]] = _family_rows(peers, "ipv4", child)
    return bgp


def _static_route(w: list[str], row: str) -> StaticRoute | None:
    """`ip route-static [vpn-instance V] <net> <mask> [vpn-instance T] [<nh>|NULL0] [preference N]`."""
    rest = w[2:]
    vpn = None
    if rest[0] == "vpn-instance":
        vpn, rest = rest[1], rest[2:]
    if len(rest) < 2:
        return None
    try:
        prefix = ipaddress.IPv4Network(f"{rest[0]}/{rest[1]}", strict=False)
    except ValueError:
        return None
    rest = rest[2:]
    target = None
    if rest[:1] == ["vpn-instance"]:
        target, rest = rest[1], rest[2:]
    next_hop = rest[0] if rest and rest[0] != "preference" else None
    preference = int(rest[rest.index("preference") + 1]) if "preference" in rest else None
    return StaticRoute(vpn, prefix, target, next_hop, preference, row)


def model_of(version: str) -> str | None:
    """`HUAWEI CE6881H-48S6CQ uptime is ...` (display version, a test switch)."""
    found = re.search(r"^HUAWEI (\S+) uptime is", version, re.MULTILINE)
    return found.group(1) if found else None


def software_of(version: str) -> str | None:
    found = re.search(r"\(([^()]*V\d{3}R\d{3}C\d{2}\w*)\)", version)
    return found.group(1) if found else None


def parse_lldp(text: str) -> list[tuple[str, str, str]]:
    """`display lldp neighbor brief`: Local Interface, Exptime(s), Neighbor Interface, Neighbor
    Device (header seen on a test switch; rows unverified: no neighbour on the test switch)."""
    rows = []
    for line in text.splitlines():
        w = line.split()
        if len(w) >= 4 and PORT.fullmatch(w[0]) and w[1].isdigit():
            rows.append((w[0], w[2], " ".join(w[3:])))
    return rows


def parse_switch(config: str, version: str = "", lldp: str = "") -> Switch:
    root = tree(config)
    name = next((c.words[1] for c in root.children if c.words[0] == "sysname"), None)
    if name is None:
        raise ImportParseError("no sysname in the running config")
    interfaces: dict[str, Interface] = {}
    bridge_domains: dict[int, BridgeDomain] = {}
    vpns: dict[str, VpnInstance] = {}
    nve, dfs, bgp = None, None, None
    ospf: dict[int, dict] = {}
    statics: list[StaticRoute] = []
    vlans: tuple[int, ...] = ()
    vlan_descriptions: dict[int, str] = {}
    ntp, ntp_vpns = [], set()
    snmp = {"location": None, "contact": None, "community": False}
    v_stp = evpn_overlay = False
    for node in root.children:
        w, row = node.words, node.row
        if w[0] == "interface" and w[1].startswith("Nve"):
            nve = {"name": w[1], "source": None, "pip_source": None, "pip_peer": None, "mac": None, "vnis": []}
            for child in node.children:
                c = child.words
                if c[0] == "source":
                    nve["source"] = c[1]
                elif c[0] == "pip-source":
                    nve["pip_source"], nve["pip_peer"] = c[1], c[3]
                elif c[0] == "mac-address":
                    nve["mac"] = c[1]
                elif c[0] == "vni":
                    nve["vnis"].append(int(c[1]))
        elif w[0] == "interface":
            interfaces[w[1]] = _interface(node)
        elif w[0] == "bridge-domain":
            bridge_domains[int(w[1])] = _bridge_domain(node)
        elif w[:2] == ["ip", "vpn-instance"]:
            vpns[w[2]] = _vpn_instance(node)
        elif w[0] == "dfs-group":
            dfs = Dfs(int(w[1]))
            for child in node.children:
                c = child.words
                if c[:4] == ["dual-active", "detection", "source", "ip"]:
                    # `... source ip <ip> [vpn-instance <v>] peer <ip>`
                    dfs.source = c[4]
                    dfs.peer = c[c.index("peer") + 1] if "peer" in c[5:-1] else None
                    dfs.vpn = c[c.index("vpn-instance") + 1] if "vpn-instance" in c[5:-1] else None
                elif c[0] == "priority":
                    dfs.priority = int(c[1])
                elif c[0] == "authentication-mode":
                    dfs.keyed = True
        elif w[0] == "bgp" and len(w) == 2 and w[1].isdigit():
            bgp = _bgp(node)
        elif w[0] == "ospf" and len(w) >= 2 and w[1].isdigit():
            entry = {"router_id": None, "vpn": None, "areas": [a.words[1] for a in node.find("area")]}
            if "router-id" in w:
                entry["router_id"] = w[w.index("router-id") + 1]
            if "vpn-instance" in w:
                entry["vpn"] = w[w.index("vpn-instance") + 1]
            ospf[int(w[1])] = entry
        elif w[:2] == ["ip", "route-static"]:
            route = _static_route(w, row)
            if route is not None and route.vpn != MANAGEMENT_VPN:
                statics.append(route)
        elif w[:2] == ["vlan", "batch"]:
            vlans = vlan_numbers(w[2:])
        elif w[0] == "vlan" and len(w) == 2 and w[1].isdigit():
            for child in node.find("description"):
                vlan_descriptions[int(w[1])] = child.row.split(" ", 1)[1]
        elif w[:2] == ["ntp", "unicast-server"]:
            ntp.append(w[2])
            ntp_vpns.add(w[w.index("vpn-instance") + 1] if "vpn-instance" in w else None)
        elif w[:3] == ["snmp-agent", "sys-info", "location"]:
            snmp["location"] = row.split(" ", 3)[3]
        elif w[:3] == ["snmp-agent", "sys-info", "contact"]:
            snmp["contact"] = row.split(" ", 3)[3]
        elif w[:3] == ["snmp-agent", "community", "read"]:
            snmp["community"] = True
        elif row == "stp v-stp enable":
            v_stp = True
        elif row == "evpn-overlay enable":
            evpn_overlay = True
    if len(ntp_vpns) > 1:
        raise ImportParseError(f"{name}: NTP servers in more than one VPN instance")
    return Switch(
        name=name, model=model_of(version), version=software_of(version), interfaces=interfaces,
        bridge_domains=bridge_domains, vpn_instances=vpns, nve=nve, dfs=dfs, bgp=bgp, ospf=ospf,
        static_routes=statics, vlans=vlans, vlan_descriptions=vlan_descriptions, v_stp=v_stp,
        evpn_overlay=evpn_overlay, ntp_servers=tuple(ntp), ntp_vpn=next(iter(ntp_vpns), None) if ntp else None,
        snmp=snmp, lldp=parse_lldp(lldp), config=config,
    )
