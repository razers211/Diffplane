"""Fabric definition: the YAML format the seed script loads into NetBox."""

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# Huawei port name prefix -> NetBox interface type.
_PORT_TYPES = {
    "GE": "1000base-t",
    "10GE": "10gbase-x-sfpp",
    "25GE": "25gbase-x-sfp28",
    "40GE": "40gbase-x-qsfpp",
    "100GE": "100gbase-x-qsfp28",
    "400GE": "400gbase-x-qsfpdd",
}
_PORT_RE = re.compile(r"(\d{0,3}GE)\d+/\d+/\d+")

LEAF_ROLES = ("leaf", "border-leaf")
# Fabric-wide: distributed anycast gateways on the leaves, or one
# gateway pair (M-LAG) holding every gateway while the other leaves stay layer 2.
ARCHITECTURES = ("symmetric", "centralized")


class FabricError(ValueError):
    pass


def interface_type(name: str) -> str:
    if re.fullmatch(r"MEth\d+/\d+/\d+", name):
        return "1000base-t"
    if re.fullmatch(r"Eth-Trunk\d+", name):
        return "lag"
    if re.fullmatch(r"(LoopBack|Vlanif)\d+", name):
        return "virtual"
    match = _PORT_RE.fullmatch(name)
    if match and match.group(1) in _PORT_TYPES:
        return _PORT_TYPES[match.group(1)]
    raise FabricError(f"unknown interface name format: {name!r}")


@dataclass(frozen=True)
class Spine:
    name: str
    model: str


@dataclass(frozen=True)
class MlagTrunk:
    """A server-facing Eth-Trunk on both leaves of a pair. Its M-LAG ID is the trunk number."""

    trunk: int
    # The same member ports on both leaves.
    members: tuple[str, ...]

    @property
    def name(self) -> str:
        return f"Eth-Trunk{self.trunk}"


@dataclass(frozen=True)
class LeafPair:
    """An M-LAG pair: two leaves, or the two spines of a centralized fabric's gateway pair."""

    leaves: tuple[str, str]
    model: str
    role: str
    peer_link_trunk: int
    # The same port names on both leaves, cabled port to port.
    peer_link_members: tuple[str, ...]
    mlag_trunks: tuple[MlagTrunk, ...] = ()

    @property
    def peer_link_name(self) -> str:
        return f"Eth-Trunk{self.peer_link_trunk}"


@dataclass(frozen=True)
class Link:
    spine: str
    spine_port: str
    leaf: str
    leaf_port: str
    unnumbered: bool = False   # imported fabrics only: no /30, `ip address unnumbered`


@dataclass(frozen=True)
class Services:
    ntp_servers: tuple[str, ...]
    snmp_location: str | None = None
    snmp_contact: str | None = None
    # VRF the NTP servers are reached through (e.g. the management VRF); None = global.
    ntp_vpn_instance: str | None = None
    # VLAN of the Vlanif over the peer-link that carries the VXLAN bypass between M-LAG peers.
    mlag_bypass_vlan: int | None = None
    # Anycast gateway MAC, the same on every VBDIF of the fabric.
    gateway_mac: str | None = None
    architecture: str = "symmetric"
    # Centralized only: the M-LAG pair that holds the gateways (names sorted).
    gateway_pair: tuple[str, str] | None = None

    def config_context(self) -> dict:
        """Data for the fabric's NetBox config context, read by the generators."""
        snmp = {k: v for k, v in (("location", self.snmp_location), ("contact", self.snmp_contact)) if v}
        fabric = {"ntp_servers": list(self.ntp_servers), "snmp": snmp}
        if self.ntp_vpn_instance:
            fabric["ntp_vpn_instance"] = self.ntp_vpn_instance
        if self.mlag_bypass_vlan is not None:
            fabric["mlag"] = {"bypass_vlan": self.mlag_bypass_vlan}
        if self.gateway_mac is not None:
            fabric["gateway_mac"] = self.gateway_mac
        fabric["architecture"] = self.architecture
        if self.gateway_pair is not None:
            fabric["gateway_pair"] = list(self.gateway_pair)
        return {"fabric": fabric}


@dataclass(frozen=True)
class Fabric:
    name: str
    asn: int
    loopback_prefix: ipaddress.IPv4Network
    vtep_prefix: ipaddress.IPv4Network
    p2p_prefix: ipaddress.IPv4Network
    # /30 per M-LAG pair for the bypass Vlanif over the peer-link.
    mlag_bypass_prefix: ipaddress.IPv4Network | None
    # Segment gateway subnets are allocated from here.
    gateways_prefix: ipaddress.IPv4Network | None
    spines: tuple[Spine, ...]
    leaf_pairs: tuple[LeafPair, ...]
    links: tuple[Link, ...]
    services: Services
    # Centralized fabrics only: two spines forming the gateway M-LAG pair (role "spine").
    spine_pair: LeafPair | None = None
    # The PIP layout (an imported fabric): each M-LAG switch's PIP on LoopBack1 from here,
    # the pair's VTEP on LoopBack2 (generators/fabric.py pip_layout).
    pip_prefix: ipaddress.IPv4Network | None = None

    @property
    def mlag_pairs(self) -> tuple[LeafPair, ...]:
        """Every M-LAG pair: the leaf pairs and the spine gateway pair."""
        return self.leaf_pairs + ((self.spine_pair,) if self.spine_pair else ())

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9-]+", "-", self.name.lower()).strip("-")


def load(path: str | Path) -> Fabric:
    return parse(yaml.safe_load(Path(path).read_text()))


def parse(data: dict, settings_only: bool = False) -> Fabric:
    """A fabric definition. `settings_only`: a fabric made in the GUI, checked before its
    switches exist; they come through Add switches, and so may a centralized gateway pair."""
    if "management" in data:
        # The GUI owns status and management addresses.
        raise FabricError("management addresses are set in the GUI (device page, Activate), not in the fabric YAML")
    if settings_only:   # its switches come later, through Add switches
        data = {**data, "spines": [], "leaf_pairs": [], "links": []}
    try:
        fabric = _build(data)
    except (KeyError, TypeError) as exc:
        raise FabricError(f"malformed fabric definition: {exc!r}") from exc
    _validate(fabric, settings_only)
    return fabric


def _network(value: str, what: str) -> ipaddress.IPv4Network:
    try:
        return ipaddress.IPv4Network(value)
    except ValueError as exc:
        raise FabricError(f"{what}: {exc}") from exc


def _interface(value: str, what: str) -> ipaddress.IPv4Interface:
    try:
        address = ipaddress.IPv4Interface(value)
    except ValueError as exc:
        raise FabricError(f"{what}: {exc}") from exc
    if address.network.prefixlen == 32:
        raise FabricError(f"{what}: give the address with its subnet mask, e.g. 10.0.0.1/24")
    return address


def _pair(p: dict, devices: str, role: str) -> LeafPair:
    return LeafPair(
        leaves=tuple(p[devices]),
        model=p.get("model", ""),
        role=role,
        peer_link_trunk=int(p["peer_link"]["trunk"]),
        peer_link_members=tuple(p["peer_link"]["members"]),
        mlag_trunks=tuple(
            MlagTrunk(trunk=int(t["trunk"]), members=tuple(t["members"])) for t in p.get("mlag_trunks") or []
        ),
    )


def _build(data: dict) -> Fabric:
    fabric = data["fabric"]
    prefixes = fabric["prefixes"]
    services = data["services"]
    snmp = services.get("snmp") or {}
    return Fabric(
        name=fabric["name"],
        asn=int(fabric["asn"]),
        loopback_prefix=_network(prefixes["loopback"], "loopback prefix"),
        vtep_prefix=_network(prefixes["vtep"], "vtep prefix"),
        p2p_prefix=_network(prefixes["p2p"], "p2p prefix"),
        mlag_bypass_prefix=(
            _network(prefixes["mlag_bypass"], "mlag_bypass prefix") if "mlag_bypass" in prefixes else None
        ),
        gateways_prefix=_network(prefixes["gateways"], "gateways prefix") if "gateways" in prefixes else None,
        pip_prefix=_network(prefixes["pip"], "pip prefix") if prefixes.get("pip") else None,
        spines=tuple(Spine(name=s["name"], model=s["model"]) for s in data["spines"]),
        leaf_pairs=tuple(_pair({"model": p["model"], **p}, "leaves", p.get("role", "leaf")) for p in data["leaf_pairs"]),
        spine_pair=_pair(data["spine_pair"], "spines", "spine") if data.get("spine_pair") else None,
        links=tuple(
            Link(spine=l["spine"], spine_port=l["spine_port"], leaf=l["leaf"], leaf_port=l["leaf_port"])
            for l in data["links"]
        ),
        services=Services(
            ntp_servers=tuple(str(s) for s in services.get("ntp_servers") or ()),
            snmp_location=snmp.get("location"),
            snmp_contact=snmp.get("contact"),
            ntp_vpn_instance=services.get("ntp_vpn_instance"),
            mlag_bypass_vlan=(int(fabric["mlag"]["bypass_vlan"]) if fabric.get("mlag") else None),
            gateway_mac=fabric.get("gateway_mac"),
            architecture=str(fabric.get("architecture", "symmetric")),
            gateway_pair=tuple(sorted(fabric["gateway_pair"])) if fabric.get("gateway_pair") else None,
        ),
    )


def _validate(fabric: Fabric, settings_only: bool = False) -> None:
    if not 1 <= fabric.asn <= 4294967295:
        raise FabricError(f"ASN out of range: {fabric.asn}")

    pools = {
        "loopback": fabric.loopback_prefix,
        "vtep": fabric.vtep_prefix,
        "p2p": fabric.p2p_prefix,
    }
    if fabric.mlag_bypass_prefix is not None:
        pools["mlag_bypass"] = fabric.mlag_bypass_prefix
    if fabric.gateways_prefix is not None:
        pools["gateways"] = fabric.gateways_prefix
    if fabric.pip_prefix is not None:
        pools["pip"] = fabric.pip_prefix
    mac = fabric.services.gateway_mac
    if mac is not None and not re.fullmatch(r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}", str(mac)):
        raise FabricError(f"fabric.gateway_mac must look like 0000-5e00-0101, got {mac!r}")
    if mac is not None and int(str(mac)[:2], 16) & 1:
        raise FabricError(f"fabric.gateway_mac {mac} is a multicast address")
    names = list(pools)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if pools[a].overlaps(pools[b]):
                raise FabricError(f"{a} prefix {pools[a]} overlaps {b} prefix {pools[b]}")
    if fabric.p2p_prefix.prefixlen > 30:
        raise FabricError(f"p2p prefix {fabric.p2p_prefix} is too small for /30 links")
    if fabric.mlag_pairs:
        if fabric.mlag_bypass_prefix is None or fabric.services.mlag_bypass_vlan is None:
            raise FabricError("leaf pairs need fabric.mlag.bypass_vlan and fabric.prefixes.mlag_bypass")
        if fabric.mlag_bypass_prefix.prefixlen > 30:
            raise FabricError(f"mlag_bypass prefix {fabric.mlag_bypass_prefix} is too small for /30 links")
        if not 2 <= fabric.services.mlag_bypass_vlan <= 4094:
            raise FabricError(f"fabric.mlag.bypass_vlan must be 2-4094, got {fabric.services.mlag_bypass_vlan}")

    services = fabric.services
    # No servers is allowed: the platform then manages no NTP lines.
    for server in services.ntp_servers:
        try:
            ipaddress.ip_address(server)
        except ValueError as exc:
            raise FabricError(f"services.ntp_servers: {exc}") from exc
    if services.ntp_vpn_instance is not None and not re.fullmatch(r"[A-Za-z0-9_.-]+", str(services.ntp_vpn_instance)):
        raise FabricError(f"services.ntp_vpn_instance is not a valid VPN instance name: {services.ntp_vpn_instance!r}")
    for name, value in (("location", services.snmp_location), ("contact", services.snmp_contact)):
        if value is not None and (not isinstance(value, str) or "\n" in value or '"' in value):
            raise FabricError(f"services.snmp.{name} must be a single line of text without quotes")

    spines = {s.name for s in fabric.spines}
    leaves: set[str] = set()
    for pair in fabric.leaf_pairs:
        if len(pair.leaves) != 2 or pair.leaves[0] == pair.leaves[1]:
            raise FabricError(f"a leaf pair needs two distinct leaves, got {list(pair.leaves)}")
        if pair.role not in LEAF_ROLES:
            raise FabricError(f"leaf pair {list(pair.leaves)}: role must be one of {LEAF_ROLES}")
        if not pair.peer_link_members:
            raise FabricError(f"leaf pair {list(pair.leaves)}: peer-link needs at least one member")
        numbers = [t.trunk for t in pair.mlag_trunks]
        if pair.peer_link_trunk in numbers or len(set(numbers)) != len(numbers):
            raise FabricError(f"leaf pair {list(pair.leaves)}: trunk numbers must be unique, peer-link included")
        for trunk in pair.mlag_trunks:
            if not trunk.members:
                raise FabricError(f"leaf pair {list(pair.leaves)}: {trunk.name} needs at least one member")
        leaves.update(pair.leaves)
    all_names = [s.name for s in fabric.spines] + [n for p in fabric.leaf_pairs for n in p.leaves]
    duplicates = {n for n in all_names if all_names.count(n) > 1}
    if duplicates:
        raise FabricError(f"device names used more than once: {sorted(duplicates)}")

    _validate_architecture(fabric, settings_only)

    used: set[tuple[str, str]] = set()

    def claim(device: str, port: str, what: str) -> None:
        if interface_type(port) in ("lag", "virtual"):
            raise FabricError(f"{device} {port} ({what}) must be a physical port")
        if (device, port) in used:
            raise FabricError(f"{device} {port} is used more than once ({what})")
        used.add((device, port))

    for pair in fabric.mlag_pairs:
        for leaf in pair.leaves:
            for port in pair.peer_link_members:
                claim(leaf, port, "peer-link")
            for trunk in pair.mlag_trunks:
                for port in trunk.members:
                    claim(leaf, port, f"M-LAG {trunk.name}")
    for link in fabric.links:
        if link.spine not in spines:
            raise FabricError(f"link {link}: {link.spine!r} is not a spine")
        if link.leaf not in leaves:
            raise FabricError(f"link {link}: {link.leaf!r} is not a leaf")
        claim(link.spine, link.spine_port, "spine link")
        claim(link.leaf, link.leaf_port, "spine link")


def _validate_architecture(fabric: Fabric, settings_only: bool = False) -> None:
    services = fabric.services
    if fabric.spine_pair is not None:
        spines = {s.name for s in fabric.spines}
        pair = fabric.spine_pair
        if len(pair.leaves) != 2 or len(set(pair.leaves)) != 2 or not set(pair.leaves) <= spines:
            raise FabricError(f"spine_pair needs two distinct spines of the fabric, got {list(pair.leaves)}")
        if services.architecture != "centralized" or set(services.gateway_pair or ()) != set(pair.leaves):
            raise FabricError("a spine_pair is the gateway pair of a centralized fabric: set fabric.architecture "
                              "to centralized and fabric.gateway_pair to its spines")
        if not pair.peer_link_members:
            raise FabricError(f"spine_pair {list(pair.leaves)}: peer-link needs at least one member")
        numbers = [t.trunk for t in pair.mlag_trunks]
        if pair.peer_link_trunk in numbers or len(set(numbers)) != len(numbers):
            raise FabricError(f"spine_pair {list(pair.leaves)}: trunk numbers must be unique, peer-link included")
    if services.architecture not in ARCHITECTURES:
        raise FabricError(f"fabric.architecture must be one of {ARCHITECTURES}, got {services.architecture!r}")
    if services.architecture == "symmetric":
        if fabric.asn > 65535:
            # Route targets are <ASN>:<VNI>; with a 4-byte ASN the number has 2 bytes, and the
            # symmetric L3 VNI (VNI + 1,000,000) never fits.
            raise FabricError(f"ASN {fabric.asn} has 4 bytes: route targets <ASN>:<L3 VNI> cannot be formed; "
                              "a symmetric fabric needs a 2-byte ASN")
        if services.gateway_pair is not None:
            raise FabricError("fabric.gateway_pair is for a centralized fabric only")
        return
    if services.gateway_mac is None:
        raise FabricError("a centralized fabric needs fabric.gateway_mac (the gateways' MAC)")
    pair = services.gateway_pair
    if settings_only and pair is None:
        return
    if pair is None or len(pair) != 2:
        raise FabricError("a centralized fabric needs fabric.gateway_pair: the two devices of its gateway M-LAG pair")
    match = next((p for p in fabric.mlag_pairs if set(p.leaves) == set(pair)), None)
    if match is None:
        raise FabricError(f"fabric.gateway_pair {list(pair)} is not one of the fabric's M-LAG pairs")
    if match.role not in ("border-leaf", "spine"):
        raise FabricError(f"fabric.gateway_pair {list(pair)}: a gateway pair of leaves has role border-leaf")
