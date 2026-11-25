"""What generators read about a device: fabric-wide settings and its fabric topology.

Fabric-wide settings come from the site's "fabric" config context (see seed/model.py).
Loopbacks and links come from the device's NetBox interfaces, as loaded by Annet.
"""

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any

from generators import naming

LEAF_ROLES = ("leaf", "border-leaf")
# Fabric links run between fabric devices only.
FABRIC_ROLES = ("spine", *LEAF_ROLES)


class MissingFabricSettings(Exception):
    pass


@dataclass(frozen=True)
class FabricSettings:
    ntp_servers: tuple[str, ...]
    snmp_location: str | None
    snmp_contact: str | None
    ntp_vpn_instance: str | None = None
    mlag_bypass_vlan: int | None = None
    asn: int | None = None
    loopback_prefix: str | None = None   # LoopBack0 pool: BGP peers in it are fabric peers
    pip_prefix: str | None = None        # PIP pool: the PIP layout (see pip_layout)


def fabric_settings(device: Any) -> FabricSettings:
    # Fail loudly: generating without settings would remove NTP/SNMP from the device.
    data = (device.config_context or {}).get("fabric")
    if not data:
        raise MissingFabricSettings(
            f"{device.hostname} has no 'fabric' config context; run the seed for its fabric"
        )
    snmp = data.get("snmp") or {}
    return FabricSettings(
        ntp_servers=tuple(data["ntp_servers"]),
        snmp_location=snmp.get("location"),
        snmp_contact=snmp.get("contact"),
        ntp_vpn_instance=data.get("ntp_vpn_instance"),
        mlag_bypass_vlan=(data.get("mlag") or {}).get("bypass_vlan"),
        asn=data.get("asn"),
        loopback_prefix=data.get("loopback_prefix"),
        pip_prefix=data.get("pip_prefix"),
    )


@dataclass(frozen=True)
class FabricLink:
    interface: str
    address: ipaddress.IPv4Interface | None   # None: unnumbered (imported fabrics)
    peer_device: str
    peer_interface: str


def is_leaf(device: Any) -> bool:
    return device.role.slug in LEAF_ROLES


def _interface(device: Any, name: str) -> Any | None:
    return next((i for i in device.interfaces if i.name == name), None)


def loopback(device: Any, name: str) -> ipaddress.IPv4Interface | None:
    """The address on a loopback interface (LoopBack0: own; the VTEP and PIP ones: vtep_loopback,
    pip_loopback)."""
    iface = _interface(device, name)
    if iface is None or not iface.ip_addresses:
        return None
    return ipaddress.IPv4Interface(iface.ip_addresses[0].address)


def own_loopback(device: Any) -> ipaddress.IPv4Interface | None:
    """The switch's own loopback address (router ID, BGP source): LoopBack0 / Loopback0."""
    return loopback(device, naming.loopback(naming.vendor_of(device), 0))


def fabric_links(device: Any) -> list[FabricLink]:
    """Routed point-to-point links: cabled physical ports with an address (or tagged unnumbered),
    not in a LAG; and an Eth-Trunk tagged unnumbered whose members are cabled, its peer the far end of its first member's cable."""
    links = []
    for iface in device.interfaces:
        unnumbered = any(tag.slug == UNNUMBERED_TAG for tag in (iface.tags or []))
        if unnumbered and iface.type.value == "lag":
            members = sorted((m for m in device.interfaces if m.lag is not None and m.lag.name == iface.name
                              and m.connected_endpoints and m.connected_endpoints[0].device is not None),
                             key=lambda m: m.name)
            if members:
                peer = members[0].connected_endpoints[0]
                links.append(FabricLink(interface=iface.name, address=None, peer_device=peer.device.name,
                                        peer_interface=peer.name))
            continue
        if iface.lag is not None or not (iface.ip_addresses or unnumbered) or not iface.connected_endpoints:
            continue
        if iface.type.value in ("lag", "virtual") or naming.is_management(iface.name):
            continue
        peer = iface.connected_endpoints[0]
        if peer.device is None:
            continue
        links.append(
            FabricLink(
                interface=iface.name,
                address=ipaddress.IPv4Interface(iface.ip_addresses[0].address) if iface.ip_addresses else None,
                peer_device=peer.device.name,
                peer_interface=peer.name,
            )
        )
    return sorted(links, key=lambda link: link.interface)


def ip_with_mask(address: ipaddress.IPv4Interface) -> str:
    """VRP shows addresses with dotted masks: 10.0.0.1 255.255.255.255."""
    return f"{address.ip} {address.netmask}"


def vlan_list(vlans) -> str:
    """VLANs as VRP displays them in `vlan batch` and `port trunk allow-pass vlan`: consecutive
    ones as a range (a test switch: 3005 3006 is shown as `3005 to 3006`)."""
    ordered, parts = sorted(set(vlans)), []
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        parts.append(str(ordered[i]) if i == j else f"{ordered[i]} to {ordered[j]}")
        i = j + 1
    return " ".join(parts)


# -- M-LAG ---------------------------------------------------------------------------

PEER_LINK_TAG = "peer-link"
MLAG_TAG = "m-lag"
UNNUMBERED_TAG = "unnumbered"   # a fabric link port borrowing LoopBack0's address


@dataclass(frozen=True)
class Trunk:
    name: str
    number: int
    members: tuple[str, ...]


def mlag_peer_name(device: Any) -> str | None:
    """The other leaf of the device's M-LAG pair (NetBox custom field `mlag_peer`)."""
    peer = (device.custom_fields or {}).get("mlag_peer")
    if isinstance(peer, dict):
        return peer.get("name") or peer.get("display")
    return None


def related(storage: Any, names: list[str]) -> dict[str, Any]:
    """Other devices from NetBox, by name (Annet caches them per run)."""
    if not names:
        return {}
    return {d.name: d for d in storage.make_devices(sorted(set(names)))}


def all_trunks(device: Any) -> list[Trunk]:
    trunks = []
    for iface in device.interfaces:
        if iface.type.value != "lag":
            continue
        members = tuple(sorted(i.name for i in device.interfaces if i.lag is not None and i.lag.name == iface.name))
        trunks.append(Trunk(name=iface.name, number=naming.trunk_number(iface.name), members=members))
    return sorted(trunks, key=lambda t: t.number)


def _is_peer_link(device: Any, trunk: Trunk) -> bool:
    iface = _interface(device, trunk.name)
    return any(tag.slug == PEER_LINK_TAG for tag in (iface.tags or []))


def peer_link(device: Any) -> Trunk | None:
    return next((t for t in all_trunks(device) if _is_peer_link(device, t)), None)


def _is_mlag(device: Any, trunk: Trunk) -> bool:
    iface = _interface(device, trunk.name)
    return any(tag.slug == MLAG_TAG for tag in (iface.tags or []))


def mlag_trunks(device: Any) -> list[Trunk]:
    """Server-facing trunks bound to the pair's M-LAG (NetBox tag `m-lag`); their M-LAG ID is
    the trunk number."""
    return [t for t in all_trunks(device) if _is_mlag(device, t) and not _is_peer_link(device, t)]


def single_trunks(device: Any) -> list[Trunk]:
    """Eth-Trunks of one device alone (no M-LAG), on a leaf or a spine."""
    return [t for t in all_trunks(device) if not _is_mlag(device, t) and not _is_peer_link(device, t)]


def retired_trunks(device: Any) -> list[Trunk]:
    """Eth-Trunks deleted from NetBox, with their members, still to be removed from this device."""
    found = (routing_document(device).get("retired_trunks") or {}).get(device.name) or {}
    return sorted((Trunk(name=n, number=naming.trunk_number(n), members=tuple(sorted(m)))
                   for n, m in found.items()), key=lambda t: t.number)


def dfs_priority(device: Any, peer_name: str) -> int:
    """Fixed rule: the leaf whose hostname sorts first wins (unless imported otherwise)."""
    return device_value(device, "dfs_priority", 150 if device.name < peer_name else 100)


# -- values adopted from an imported fabric ------------------------------
# An imported fabric stores the values its switches use where they differ from the derived ones
# (config context `overrides-<site>`, written by the orchestrator); everywhere else, and in every
# fabric the platform made, the derived value applies.

def overrides(device: Any) -> dict:
    return _context(device).get("overrides") or {}


def fabric_value(device: Any, key: str, derived):
    return (overrides(device).get("fabric") or {}).get(key, derived)


def device_value(device: Any, key: str, derived):
    return ((overrides(device).get("devices") or {}).get(device.name) or {}).get(key, derived)


def description(device: Any, interface: str, derived: str) -> str:
    return (device_value(device, "descriptions", {}) or {}).get(interface, derived)


def trunk_description(device: Any, trunk: str) -> str | None:
    """A server trunk's description: NetBox's (port settings, adopted from the switch when they
    were turned on), else an import's adopted one, else none."""
    iface = _interface(device, trunk)
    return (iface.description if iface is not None else None) or description(device, trunk, None)


# A bridge domain carrying the management network (a VBDIF in _management_vpn_ on some switch)
# is recorded by the import per switch: its bridge domain,
# VNI, VBDIF, the VPN instances of its VBDIFs, its VLAN and L2 sub-interfaces. Every generator
# leaves them alone (never generated, never removed): management plane.
MANAGEMENT_KINDS = ("bridge_domains", "vnis", "vpn_instances", "vlans", "interfaces")


def management(device: Any, kind: str) -> list:
    return list(((device_value(device, "management", {}) or {}).get(kind)) or [])


def not_management(device: Any, kind: str) -> str:
    """A regex lookahead refusing this switch's management `kind` (empty when it has none)."""
    names = management(device, kind)
    # the name may be followed by more words (`vni <n> head-end ...`, `interface <p>.<n> mode l2`)
    return f"(?!({'|'.join(re.escape(str(n)) for n in names)})(?!\\S))" if names else ""


def left_alone_vpns(device: Any) -> list[str]:
    """This switch's management VPN instances and those the import keeps as they are (`kept_vpns`:
    VPN instances bound to no interface)."""
    return management(device, "vpn_instances") + list(device_value(device, "kept_vpns", []) or [])


def managed_vpn(device: Any, base: str) -> str:
    """An ACL VPN-instance pattern (MANAGED_VPN, SERVICE_VPN) without this switch's left-alone ones."""
    names = left_alone_vpns(device)
    keep = f"(?!({'|'.join(re.escape(n) for n in names)})(?!\\S))" if names else ""
    return f"*/{keep}{base[2:]}"


def scheme_vpns(device: Any) -> list[str]:
    """Segment VPN instances named outside the `<name>-<bd_id>` rule (an imported fabric's OOB-HOSTS...)."""
    return sorted({s["vrf"] for s in (overrides(device).get("segments") or {}).values() if s.get("vrf")})


def segment_vpn_acl(device: Any) -> str:
    """SEGMENT_VPN with this fabric's segment instances named otherwise (scheme_vpns)."""
    names = scheme_vpns(device)
    return f"*/({'|'.join([SEGMENT_VPN_PATTERN, *map(re.escape, names)])})/" if names else SEGMENT_VPN


def service_vpn(device: Any) -> str:
    """SERVICE_VPN without this switch's management instances and the segments' named otherwise."""
    names = left_alone_vpns(device) + scheme_vpns(device)
    keep = f"(?!({'|'.join(map(re.escape, names))})$)" if names else ""
    return f"*/{keep}{SERVICE_VPN[2:]}"


# -- segments ------------------------------------------------------------------------------

class UnknownSegment(Exception):
    pass


@dataclass(frozen=True)
class PortSegment:
    port: str
    vni: int
    vid: int | None   # None: untagged
    bd_id: int


def port_segments(device: Any) -> list[PortSegment]:
    """Segments attached to the device's ports (interface custom field `segments`), with
    their bridge-domain IDs from the "segments" config context."""
    known = ((device.config_context or {}).get("segments") or {})
    result = []
    for iface in device.interfaces:
        for entry in (iface.custom_fields or {}).get("segments") or []:
            segment = known.get(str(entry["vni"]))
            if segment is None:
                raise UnknownSegment(f"{device.name} {iface.name}: VNI {entry['vni']} is not a known segment")
            result.append(PortSegment(iface.name, entry["vni"], entry.get("vid"), segment["bd_id"]))
    return sorted(result, key=lambda s: (s.port, s.vni))


def subif_number(segment: PortSegment, device: Any = None) -> int:
    """Numbering: tagged -> <port>.<bd_id>, untagged -> <port>.1 (<port>.<bd_id>
    in an imported fabric)."""
    if segment.vid is None and not (device is not None and fabric_value(device, "untagged_subif", None) == "bd"):
        return 1
    return segment.bd_id


def service_scheme(device: Any, name: str) -> dict:
    """An imported fabric's own values for one service, adopted by its import: `rd`,
    the Vlanif's `mac` and `arp_proxy`, `shared_main` (the pair's shared address is the main
    `ip address`, each switch's own the `m-lag ip address`), and for the firewall's BGP session
    `connect_interface` (its Vlanif) and `bfd` (false: none). Empty elsewhere: the derived values."""
    return dict((overrides(device).get("services") or {}).get(name) or {})


def segment_scheme(device: Any, vni: int) -> dict:
    """An imported fabric's own values for one segment, adopted by its import: `vrf`
    (its VPN instance's name), `rd`, `l3_vni`, `l3_rt`, `l2_rt`, the gateway's `gateway_mac`,
    `anycast` (false: no `vxlan anycast-gateway enable`), `arp_broadcast_detect`, `arp_collect_host`,
    and `bd_arp` (`arp l2-proxy enable` and `arp collect host enable` in the bridge domain where the
    segment has no gateway). Empty everywhere else: the derived values apply."""
    return dict((overrides(device).get("segments") or {}).get(str(vni)) or {})


# -- tenants ----------------------------------------------------------------------------

L3_VNI_OFFSET = 1_000_000   # design rule: L3 VNI = L2 VNI + 1,000,000

# Ownership by kind: a leaf's VPN instances, Vlanifs and port trunk
# settings belong to the platform whether or not NetBox still lists them, so deleting a
# service removes its config. Never the management plane.
MANAGED_VPN = "*/(?!(_management_vpn_|_public_)$)\\S+/"          # every VPN instance but the system ones
# A routed segment's VPN instance is <segment name>-<bd_id>; service
# names never end in -<digits>, so the two kinds never overlap.
SEGMENT_VPN_PATTERN = r"[A-Za-z][A-Za-z0-9_-]*-\d+"
SEGMENT_VPN = f"*/{SEGMENT_VPN_PATTERN}/"                        # ACL: a segment's VPN instance
SERVICE_VPN = "*/(?!(_management_vpn_|_public_)$)(?!.*-\\d+$)\\S+/"  # ... one that is not a segment's


VPN_NAME_MAX = 31   # VRP's VPN instance name limit (a test switch: 32 is refused)


class BadSegmentName(Exception):
    pass


def segment_vpn(name: str, bd_id: int) -> str:
    return f"{name}-{bd_id}"


def is_segment_vpn(name: str | None) -> bool:
    return bool(name) and re.fullmatch(SEGMENT_VPN_PATTERN, name) is not None
FRONT_PANEL_PORT = "*/\\d*GE\\d+\\/\\d+\\/\\d+/"                 # 10GE1/0/4, 100GE1/0/1; not MEth, not .N


@dataclass(frozen=True)
class RelayServer:
    address: str
    service: str | None = None   # behind this service (the segment's firewall): its VPN instance
    segment: int | None = None   # in this peered segment (L2 VNI): its VPN instance


@dataclass(frozen=True)
class Relay:
    """DHCP relay of a routed segment: servers, the loopback number, and each gateway
    switch's relay loopback address (orchestrator/dhcp_relay.py keeps them in NetBox)."""
    servers: tuple[RelayServer, ...]
    loopback: int
    loopbacks: tuple[tuple[str, str], ...] = ()   # (switch, address/32)
    loopbacks6: tuple[tuple[str, str], ...] = ()  # (switch, address/128): DHCPv6 relay

    @property
    def interface(self) -> str:
        return f"LoopBack{self.loopback}"

    def address(self, switch: str) -> ipaddress.IPv4Interface | None:
        found = dict(self.loopbacks).get(switch)
        return ipaddress.IPv4Interface(found) if found else None

    def address6(self, switch: str) -> ipaddress.IPv6Interface | None:
        found = dict(self.loopbacks6).get(switch)
        return ipaddress.IPv6Interface(found) if found else None


@dataclass(frozen=True)
class Ipv6:
    """IPv6 of a dual-stack segment: its /64 gateway (::1), how hosts get addresses
    (slaac, dhcpv6 or none) and its DHCPv6 servers (placed as DHCP relay servers)."""
    gateway: ipaddress.IPv6Interface
    hosts: str = "slaac"
    servers: tuple[RelayServer, ...] = ()


@dataclass(frozen=True)
class RoutedSegment:
    vni: int
    bd_id: int
    name: str
    gateway: ipaddress.IPv4Interface
    peers: tuple[int, ...]      # L2 VNIs of explicitly peered segments
    firewall: str | None
    relay: Relay | None = None
    ipv6: Ipv6 | None = None
    scheme: dict = field(default_factory=dict, compare=False)   # an imported fabric's own values (segment_scheme)

    @property
    def l3_vni(self) -> int:
        return self.scheme.get("l3_vni") or self.vni + L3_VNI_OFFSET

    @property
    def vrf(self) -> str:
        return self.scheme.get("vrf") or segment_vpn(self.name, self.bd_id)

    def l3_rt(self, asn: int) -> str:
        return self.scheme.get("l3_rt") or f"{asn}:{self.l3_vni}"

    def has_l3(self, device: Any) -> bool:
        """An L3 VNI and route target: every symmetric fabric's; a centralized one's only when its
        switches have them (imported fabrics)."""
        return not centralized(device) or "l3_vni" in self.scheme


@dataclass(frozen=True)
class ServiceIpv6:
    """A service's IPv6: its Vlanif address and the firewall's (or ISP router's) next hop."""
    address: ipaddress.IPv6Interface   # on an M-LAG pair: the shared M-LAG address
    next_hop: str   # as V300 displays it (v6)
    leaf_addresses: tuple[tuple[str, str], ...] = ()   # on an M-LAG pair: each leaf's own address


@dataclass(frozen=True)
class Service:
    name: str
    device: str
    port: str | None   # None: no port of its own, its VLAN rides VLAN trunks (an imported fabric's firewall contexts)
    vlan: int
    address: ipaddress.IPv4Interface
    next_hop: str
    asn: int | None = None      # firewall ASN: eBGP session in the service instance
    kind: str = "firewall"      # or "isp": container only, its session is a routing-intent peer
    # A service on an M-LAG pair (port: an M-LAG server trunk): both leaves, each leaf's main
    # Vlanif address; `address` is then the shared M-LAG address.
    devices: tuple[str, ...] = ()
    leaf_addresses: tuple[tuple[str, str], ...] = ()
    ipv6: ServiceIpv6 | None = None
    # The VPN instance it sits in when not its own: another service's, whose
    # RD it keeps (`rd_vlan`, so the instance outlives the service that made it), or a routed
    # segment's (no RD of its own there).
    vrf_name: str | None = None
    rd_vlan: int | None = None

    @property
    def vrf(self) -> str:
        return self.vrf_name or self.name

    def on(self, name: str) -> bool:
        return name in (self.devices or (self.device,))

    def main_address(self, name: str) -> ipaddress.IPv4Interface | None:
        """This leaf's own address on a pair service's Vlanif (None: a single-leaf service)."""
        found = dict(self.leaf_addresses).get(name)
        return ipaddress.IPv4Interface(found) if found else None


def _context(device: Any) -> dict:
    return device.config_context or {}


def _relay(data: dict | None) -> Relay | None:
    """The relay loopback and its IPv4 servers (none when only DHCPv6 relays through it)."""
    if not data or not data.get("loopback"):
        return None
    return Relay(servers=tuple(RelayServer(s["address"], s.get("service"), s.get("segment")) for s in data.get("servers") or ()),
                 loopback=int(data["loopback"]), loopbacks=tuple(sorted((data.get("loopbacks") or {}).items())),
                 loopbacks6=tuple(sorted((data.get("loopbacks6") or {}).items())))


def v6(address) -> str:
    """An IPv6 address as V300 displays it: upper-case hex, compressed (a test switch deploy:
    `ipv6 address FD12:3456:789A:6::1/64`)."""
    return str(address).upper()


def v6_interface(interface: ipaddress.IPv6Interface) -> str:
    """An interface's IPv6 address and length as V300 displays them: `<ADDRESS>/<length>`."""
    return f"{v6(interface.ip)}/{interface.network.prefixlen}"


def address(value: str) -> str:
    """An intent address as V300 displays it: IPv6 upper-case, IPv4 unchanged."""
    return v6(value) if ":" in str(value) else str(value)


def _ipv6(data: dict | None) -> Ipv6 | None:
    if not data or not data.get("gateway"):
        return None
    return Ipv6(gateway=ipaddress.IPv6Interface(data["gateway"]), hosts=data.get("hosts") or "slaac",
                servers=tuple(RelayServer(s["address"], s.get("service"), s.get("segment")) for s in data.get("servers") or ()))


def _service_ipv6(data: dict | None) -> ServiceIpv6 | None:
    if not data or not data.get("address"):
        return None
    return ServiceIpv6(ipaddress.IPv6Interface(data["address"]), v6(ipaddress.IPv6Address(data["next_hop"])),
                       tuple(sorted((data.get("addresses") or {}).items())))


def routed_segments(device: Any) -> dict[int, RoutedSegment]:
    """Every routed segment of the device's fabric, by L2 VNI."""
    result = {}
    for vni, entry in (_context(device).get("segments") or {}).items():
        routing = entry.get("routing")
        if routing:
            segment = RoutedSegment(
                vni=int(vni), bd_id=entry["bd_id"], name=entry["name"],
                gateway=ipaddress.IPv4Interface(routing["gateway"]),
                peers=tuple(routing.get("peers") or ()), firewall=routing.get("firewall"),
                relay=_relay(routing.get("relay")), ipv6=_ipv6(routing.get("ipv6")),
                scheme=segment_scheme(device, int(vni)),
            )
            if "vrf" not in segment.scheme and (not is_segment_vpn(segment.vrf) or len(segment.vrf) > VPN_NAME_MAX):
                # Never a broken VPN instance line: the orchestrator refuses such names.
                raise BadSegmentName(f"segment {segment.name!r} cannot name a VPN instance ({segment.vrf!r}); rename it")
            result[int(vni)] = segment
    return result


def services(device: Any) -> dict[str, Service]:
    return {
        name: Service(name, s["device"], s["port"], s["vlan"], ipaddress.IPv4Interface(s["address"]), s["next_hop"],
                      s.get("asn"), s.get("kind") or "firewall", tuple(s.get("devices") or ()),
                      tuple(sorted((s.get("addresses") or {}).items())), _service_ipv6(s.get("ipv6")),
                      s.get("vrf") if s.get("vrf") != name else None, s.get("rd_vlan"))
        for name, s in (_context(device).get("services") or {}).items()
    }


def in_segment(device: Any, service: Service) -> bool:
    """A service sitting in a routed segment's VPN instance: no instance of its
    own, its Vlanif in the segment's."""
    return service.vrf_name is not None and any(s.vrf == service.vrf for s in routed_segments(device).values())


# -- architecture -----------------------------------------------------

def centralized(device: Any) -> bool:
    """A fabric-wide setting: gateways on one M-LAG pair (centralized) or on every leaf (symmetric)."""
    return ((_context(device).get("fabric") or {}).get("architecture") or "symmetric") == "centralized"


def is_gateway(device: Any) -> bool:
    """Holds VBDIF gateways and segment VPN instances: every leaf (symmetric), or the gateway
    pair alone (centralized; the other leaves are layer 2 only)."""
    if centralized(device):
        return device.name in ((_context(device).get("fabric") or {}).get("gateway_pair") or ())
    return is_leaf(device)


def pip_layout(device: Any) -> bool:
    """An M-LAG switch of a fabric with a PIP pool (imported): its own
    PIP on LoopBack1 (not in OSPF) and the pair's shared VTEP on LoopBack2, instead of the PIP on
    LoopBack0 and the VTEP on LoopBack1. A lone leaf keeps its own VTEP on LoopBack1."""
    return bool((_context(device).get("fabric") or {}).get("pip_prefix")) and mlag_peer_name(device) is not None


def vtep_loopback(device: Any) -> str:
    return naming.loopback(naming.vendor_of(device), 2 if pip_layout(device) else 1)


def pip_loopback(device: Any) -> str:
    """Where `pip-source` and the bypass route take the PIP from (the pair has one layout)."""
    return naming.loopback(naming.vendor_of(device), 1 if pip_layout(device) else 0)


def has_vtep(device: Any) -> bool:
    """A VXLAN tunnel endpoint: an overlay device with a VTEP address (an M-LAG pair's shared one,
    or a lone leaf's own)."""
    return has_overlay(device) and loopback(device, vtep_loopback(device)) is not None


def has_overlay(device: Any) -> bool:
    """Runs M-LAG, the VTEP and the tenant config: a leaf, or a device of the gateway pair
    (a spine pair can be the gateways of a centralized fabric), or any M-LAG spine pair of
    a centralized fabric (there spines also act as border leaves; a pair that
    is not the gateway pair is layer 2 like the other leaves). Single spines do not."""
    return is_leaf(device) or is_gateway(device) or (centralized(device) and mlag_peer_name(device) is not None)


def gateway_segments(device: Any) -> list[RoutedSegment]:
    """Routed segments with a gateway on this device: those attached here (symmetric), or
    every routed segment of the fabric on the gateway pair (centralized)."""
    if not centralized(device):
        return local_routed(device)
    if not is_gateway(device):
        return []
    return sorted(routed_segments(device).values(), key=lambda s: s.bd_id)


def local_routed(device: Any) -> list[RoutedSegment]:
    """Routed segments attached to one of the device's ports: they get a gateway here."""
    routed = routed_segments(device)
    vnis = {s.vni for s in port_segments(device)}
    return sorted((routed[v] for v in vnis if v in routed), key=lambda s: s.bd_id)


def local_services(device: Any) -> list[Service]:
    """Services on this leaf: its own, and its M-LAG pair's (identical on both leaves)."""
    return sorted((s for s in services(device).values() if s.on(device.name)), key=lambda s: s.name)


def firewalled_here(device: Any) -> list[RoutedSegment]:
    """Routed segments whose firewall service is on this device: their routes are stitched here."""
    names = {s.name for s in local_services(device)}
    return sorted((s for s in routed_segments(device).values() if s.firewall in names), key=lambda s: s.bd_id)


def is_border(device: Any) -> bool:
    """A border leaf of a symmetric fabric: it only routes (firewall and ISP
    services, EVPN type-5 towards the fabric) and has every routed segment's VPN instance, but
    no bridge domain, VBDIF or attachment. In a centralized fabric the border is the gateway
    pair, so the role means nothing more there."""
    return device.role.slug == "border-leaf" and not centralized(device)


def tenant_vrfs(device: Any) -> list[RoutedSegment]:
    """Segment VPN instances the device needs: local gateways plus segments firewalled here;
    on a border leaf every routed segment, as soon as it exists (centralized: every routed
    segment, on the gateway pair only)."""
    if centralized(device):
        return gateway_segments(device)
    if is_border(device):
        return sorted(routed_segments(device).values(), key=lambda s: s.bd_id)
    found = {s.vni: s for s in local_routed(device) + firewalled_here(device) + hosting_here(device)}
    return sorted(found.values(), key=lambda s: s.bd_id)


def hosting_here(device: Any) -> list[RoutedSegment]:
    """Routed segments whose VPN instance holds a service on this device."""
    vrfs = {s.vrf for s in local_services(device) if s.vrf_name is not None}
    return sorted((s for s in routed_segments(device).values() if s.vrf in vrfs), key=lambda s: s.bd_id)


# -- ownership helpers (split by object) -------------------------------

def ipv4_regex(network: str) -> str:
    """A regular expression matching exactly the addresses of an IPv4 network, for ACLs."""
    net = ipaddress.IPv4Network(network)
    first, last = net.network_address.packed, net.broadcast_address.packed
    parts = []
    for a, b in zip(first, last):
        if a == b:
            parts.append(str(a))
        elif (a, b) == (0, 255):
            parts.append("\\d+")
        else:
            parts.append("(?:" + "|".join(str(n) for n in range(a, b + 1)) + ")")
    return "\\.".join(parts)


def routing_document(device: Any) -> dict:
    """The fabric's routing intent (orchestrator/routing_intent.py), as the config context has it."""
    return (_context(device).get("routing") or {})


def retired_services(device: Any) -> list[str]:
    """VPN instances of firewall services deleted from NetBox whose routes and peers on this device
    are still to be removed."""
    return sorted({r[0] for r in retired_firewalls(device)})


def retired_firewalls(device: Any) -> list[tuple[str, ...]]:
    """Each retired firewall as (VPN instance, next hops...): "<instance> <next hop> [<IPv6 next hop>]"
    since services share instances; an older entry is the instance name alone."""
    return [tuple(e.split()) for e in (routing_document(device).get("retired_services") or {}).get(device.name) or []]


# -- general routing intent -------------------------------------------------------------

class MissingVpnInstance(Exception):
    pass


def pair_key(a: str, b: str) -> str:
    """The routing intent key of an M-LAG pair: both names, sorted, joined by '+'."""
    return "+".join(sorted((a, b)))


def intent_key(device: Any) -> str:
    """Where this device's routing intent lives: its M-LAG pair's key (the same objects on
    both leaves), or its own name."""
    peer = mlag_peer_name(device) if has_overlay(device) else None
    return pair_key(device.name, peer) if peer else device.name


def _merged(docs: list[dict]) -> dict:
    """A pair's routing plus the switch's own: named objects by name, keyed ones listed once
    (the orchestrator refuses an object both have unless they are identical)."""
    out: dict = {}
    for doc in docs:
        for kind, value in (doc or {}).items():
            if isinstance(value, dict):
                out[kind] = {**out.get(kind, {}), **value}
            else:
                out[kind] = [*out.get(kind, []), *(v for v in value if v not in out.get(kind, []))]
    return out


def _routing_of(device: Any, part: str) -> dict:
    """A switch of an M-LAG pair has the pair's routing (on both switches) and its own (e.g.
    each ISP link's session on its own border switch); an unpaired one its own."""
    docs = routing_document(device).get(part) or {}
    key = intent_key(device)
    return _merged([docs.get(key) or {}, *([docs.get(device.name) or {}] if key != device.name else [])])


def device_routing(device: Any) -> dict:
    """This device's routing intent: prefix_lists, as_path_filters, community_filters,
    route_policies, static_routes, bgp_peers, aggregates, ospf (absent keys are empty)."""
    return _routing_of(device, "devices")


def retired_routing(device: Any) -> dict:
    """Routing objects deleted from intent, still owned until a verified deploy removed them."""
    return _routing_of(device, "retired")


def owned(device: Any, kind: str) -> list:
    """Intent and retired objects of one kind: what the routing generators' ACLs list."""
    current, retired = device_routing(device).get(kind), retired_routing(device).get(kind)
    if isinstance(current or retired, dict) or kind in ("prefix_lists", "as_path_filters", "community_filters",
                                                         "route_policies"):
        return sorted(set(current or {}) | set(retired or {}))
    return [*(current or []), *(retired or [])]


def platform_vpns(device: Any) -> set[str]:
    """VPN instances the platform creates on this device (TenantVrfs): segment instances and
    the services hosted here. Routing objects may only use these (or the global table)."""
    if not has_overlay(device):
        return set()
    return {s.vrf for s in tenant_vrfs(device)} | {s.vrf for s in local_services(device)}


def check_vpn(device: Any, vpn: str | None, what: str) -> None:
    if vpn is not None and vpn not in platform_vpns(device):
        raise MissingVpnInstance(f"{device.name}: {what} uses VPN instance {vpn}, which the platform does not "
                                 "create on this device (a routed segment attached here or a service hosted here)")


# -- services on an M-LAG pair (the dual-active gateway is for services only) --

def mlag_mac(address: ipaddress.IPv4Address) -> str:
    """The MAC of a pair service's Vlanif, the same on both leaves: 02 01 + the M-LAG address (locally
    administered; 02 00 + address is the NVE MAC). VRP format hhhh-hhhh-hhhh."""
    digits = "0201" + address.packed.hex()
    return "-".join(digits[i:i + 4] for i in range(0, 12, 4))
