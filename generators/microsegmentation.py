import ipaddress
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from annet.generators import PartialGenerator

from generators.fabric import (RoutedSegment, device_value, gateway_segments, has_overlay, local_services, port_segments, routed_segments, services,
                               tenant_vrfs, v6)

POLICY = "FABRIC"
PROTOCOLS = {"any": None, "tcp": 6, "udp": 17, "icmp": 1}
PROTOCOL_NAMES = {6: "tcp", 17: "udp"}   # V300 shows these two by name; others as numbers


@dataclass(frozen=True)
class Rule:
    version: int
    source: int
    destination: int
    protocol: int | None
    side: str                     # which end the ports belong to: "destination" (forward) or "source" (reply)
    ports: tuple[int, int] | None


def contract_rules(source: int, destination: int, versions: set[int], services: list[dict]) -> list[Rule]:
    """A contract's rules: per IP version both EPGs have and per service, the forward rule and
    its reply (GBP rules are stateless). ICMP is protocol 58 in IPv6."""
    rules = []
    for version in sorted(versions):
        for service in services:
            protocol = service["protocol"]
            number = PROTOCOLS[protocol] if isinstance(protocol, str) else protocol
            if protocol == "icmp" and version == 6:
                number = 58
            ports = tuple(service["ports"]) if service.get("ports") else None
            rules.append(Rule(version, source, destination, number, "destination", ports))
            rules.append(Rule(version, destination, source, number, "source", ports))
    return rules


def rule_line(rule: Rule) -> list:
    line = ["permit", f"ipv{rule.version}", "source-segment", rule.source, "destination-segment", rule.destination]
    if rule.protocol is not None:
        line += ["protocol", PROTOCOL_NAMES.get(rule.protocol, rule.protocol)]
    if rule.ports is not None:
        a, b = rule.ports
        line += [f"{rule.side}-port", *(("eq", a) if a == b else ("range", a, b))]
    return line


def classifier(contract: dict) -> str:
    """Named by EPG IDs, which never change (names do), at most 15 characters of the 31 allowed."""
    return f"GBP-{contract['from']}-{contract['to']}"


@dataclass(frozen=True)
class Epg:
    id: int
    name: str
    segment: RoutedSegment
    group: dict | None            # a named group's entry; None for the segment's own EPG
    inside: str

    @property
    def versions(self) -> set[int]:
        return {4} | ({6} if self.segment.ipv6 is not None else set())

    def networks(self) -> list:
        if self.group is None:
            return self.segment_networks()
        # A group keeps only prefixes inside its segment's subnets as they are now (a segment routed
        # again gets a new subnet), never a whole subnet: V300 takes a prefix in one EPG only.
        own = self.segment_networks()
        nets = [ipaddress.ip_network(p) for p in self.group.get("prefixes") or ()]
        return [n for n in nets if n not in own and any(n.version == s.version and n.subnet_of(s) for s in own)]

    def segment_networks(self) -> list:
        nets = [self.segment.gateway.network]
        if self.segment.ipv6 is not None:
            nets.append(self.segment.ipv6.gateway.network)
        return nets


def document(device: Any) -> dict:
    return ((device.config_context or {}).get("fabric") or {}).get("microsegmentation") or {}


def mode(device: Any) -> str:
    return document(device).get("mode") or "off"


def epgs(device: Any) -> dict[int, Epg]:
    """Active EPGs: those whose segment is routed (others stay in intent, inactive)."""
    routed = routed_segments(device)
    result = {}
    for key, entry in (document(device).get("epgs") or {}).items():
        segment = routed.get(int(entry["segment"]))
        if segment is None:
            continue
        name = f"{segment.name}-{entry['name']}" if entry.get("name") else segment.name
        result[int(key)] = Epg(int(key), name, segment, entry if entry.get("name") else None, entry.get("inside") or "permit")
    return result


def member_vpns(segment: RoutedSegment, device: Any) -> list[tuple[str, bool]]:
    """(VPN instance, has IPv6) a segment's prefixes are written with on this device: its own, its
    peered segments' and its firewall service's, where this device has them. Huawei looks the
    destination EPG up with the source VPN instance between instances (security guide).
    shortcut: routes leaked by routing intent (static routes between instances) are not followed;
    add them here if a deployment needs them."""
    routed = routed_segments(device)
    present = {s.vrf: s.ipv6 is not None for s in tenant_vrfs(device)}
    present |= {s.vrf: s.ipv6 is not None for s in local_services(device)}
    peers = set(segment.peers) | {s.vni for s in routed.values() if segment.vni in s.peers}
    wanted = [segment.vrf] + sorted(routed[v].vrf for v in peers if v in routed)
    if segment.firewall:
        firewall = services(device).get(segment.firewall)
        wanted.append(firewall.vrf if firewall else segment.firewall)
    return [(vpn, present[vpn]) for vpn in dict.fromkeys(wanted) if vpn in present]


def mask(network) -> str:
    """V300's stored form: a dotted IPv4 mask, a full upper-case IPv6 mask."""
    return str(network.netmask) if network.version == 4 else v6(network.netmask)


class Microsegmentation(PartialGenerator):
    """Huawei microsegmentation, V300R025 and later.

    Every routed segment is an EPG (its gateway subnets), named groups of prefixes and host MACs
    sit inside segments; contracts permit EPG -> EPG for services, each with its reply rule.
    Between EPGs: deny in Enforce, permit in Learn; inside an EPG permit unless it says deny;
    hosts in no EPG are permitted (the switch's default). Written on every overlay device: every
    leaf of a symmetric fabric; in a centralized one (unsupported by Huawei)
    the gateway pair with every member, and the layer-2 leaves with the contracts and the groups'
    MAC members of their bridge domains only (no VPN instance there, so no IP member), so traffic
    inside one subnet that never reaches the gateway is filtered too. Each contract is a classifier and a behavior named by its EPG IDs
    in the one global policy FABRIC, with its stored precedence; rules 10, 20, ... Forms as V300R025
    stores them.

    Owns every `traffic-segment`, `segment ...` and `segment-policy` line by kind (): a
    hand-made EPG or policy shows as a removal.
    """

    TAGS = ["microsegmentation", "security"]

    def acl_huawei(self, device: Any) -> str:
        if not has_overlay(device):
            return ""
        return "\n".join([
            "traffic-segment ~ %cant_delete=0",
            "    ~ %global",
            "segment ~ %cant_delete=0",
            "    ~ %global",
            "segment-policy ~",
        ])

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        current = mode(device)
        if current == "off" and has_overlay(device) and device_value(device, "traffic_segment_enable", False):
            yield "traffic-segment enable"   # an imported switch has it alone
        if current == "off" or not has_overlay(device):
            return
        groups = epgs(device)
        # MAC members where the bridge domain is: a port's segment, or a gateway's (centralized: every routed one)
        bridge_domains = {s.vni for s in port_segments(device)} | {s.vni for s in gateway_segments(device)}
        yield "traffic-segment enable"
        if current == "learn":
            yield "traffic-segment default-policy permit"
        else:   # written out, though the YANG's default (owner, 2026-11-20)
            yield "traffic-segment default-policy deny"
        # No global `segment-policy statistics enable`: V300R025C10 has none (it reads "statistics" as a
        # policy name, eNSP 2026-11-20); still owned, so a switch that has it loses it.
        for epg in sorted(groups.values(), key=lambda e: e.id):
            members = []
            vpns = member_vpns(epg.segment, device)
            for net in epg.networks():
                for vpn, has6 in vpns:
                    if net.version == 4:
                        members.append(("segment-member ip", str(net.network_address), mask(net), "vpn-instance", vpn))
                    elif has6:   # V300 refuses an IPv6 member in an instance without IPv6
                        members.append(("segment-member ipv6", v6(net.network_address), mask(net), "vpn-instance", vpn))
            if epg.group is not None and epg.segment.vni in bridge_domains:
                members += [("segment-member mac-address", mac, "bridge-domain", epg.segment.bd_id)
                            for mac in epg.group.get("macs") or ()]
            if not members:   # nothing of it here (a classifier may still name it: Huawei's guide)
                continue
            with self.block("traffic-segment segment-id", epg.id, "segment-name", epg.name, "intra-epg-behavior", epg.inside):
                yield from members
        contracts = [c for c in document(device).get("contracts") or () if c["from"] in groups and c["to"] in groups]
        contracts.sort(key=lambda c: c["precedence"])
        for contract in contracts:
            with self.block("segment behavior", classifier(contract)):
                yield "statistics enable"
        for contract in contracts:
            source, destination = groups[contract["from"]], groups[contract["to"]]
            rules = contract_rules(source.id, destination.id, source.versions & destination.versions, contract["services"])
            with self.block("segment classifier", classifier(contract)):
                for number, rule in enumerate(rules, 1):
                    yield ("rule", number * 10, *rule_line(rule))
        if contracts:
            with self.block("segment policy", POLICY):
                for contract in contracts:
                    yield "classifier", classifier(contract), "behavior", classifier(contract), "precedence", contract["precedence"]
            yield "segment-policy", POLICY
