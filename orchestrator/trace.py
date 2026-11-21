"""Path trace: from host A to host B within one fabric, by IP or MAC.

Each end is located from intent (the segment or service subnet an IP is in) and from the switches'
tables read on demand (ARP gives an IP's MAC, the MAC tables give the switch and port; a remote MAC
entry only names the VTEP it came from). The path then follows the intended model: bridged in one
segment (VXLAN between two VTEPs), or routed through the pre-deploy checks' VPN route model (gateways,
peering, firewall defaults, routing-intent statics): symmetric fabrics route on the source's leaf
and carry the packet over the destination instance's L3 VNI, centralized ones through the gateway
pair. A path into a firewall service ends at the firewall (its inside is unknown), a next hop in an
ISP service ends at the ISP router. Every hop says where it comes from. Forwarding itself is
unverified (one lab switch).
"""

import ipaddress
import re
from dataclasses import dataclass, field

from orchestrator import documents
from orchestrator.annet import PARALLEL
from orchestrator.checks import Model, _net, _tables, vpn_of

L3_VNI_OFFSET = 1_000_000   # design rule: L3 VNI = L2 VNI + 1,000,000
DELIVERED, FIREWALL, ISP, GATEWAY, DROP, UNKNOWN = "delivered", "firewall", "isp", "gateway", "drop", "unknown"


@dataclass
class Fabric:
    model: Model                          # segments, services, routing intent (orchestrator/checks.py)
    architecture: str = "symmetric"       # or centralized
    gateways: list[str] = field(default_factory=list)   # centralized: the gateway pair
    vteps: dict[str, str] = field(default_factory=dict)  # switch -> its VTEP address (shared by a pair)
    switches: list[str] = field(default_factory=list)    # every switch of the fabric (a switch as an end)
    borders: list[str] = field(default_factory=list)     # symmetric: its border leaves


@dataclass
class Tables:
    """What the switches answered: switch -> entries."""
    arp: dict[str, list[dict]] = field(default_factory=dict)    # {vpn, ip, mac, type, interface}
    macs: dict[str, list[dict]] = field(default_factory=dict)   # {bd, mac, interface, peer}


def mac_of(text: str) -> str | None:
    """A MAC in VRP's form (hhhh-hhhh-hhhh) from any usual spelling, or None."""
    digits = re.sub(r"[^0-9a-fA-F]", "", text)
    if len(digits) != 12 or not re.fullmatch(r"[0-9a-fA-F:.\- ]+", text.strip()):
        return None
    d = digits.lower()
    return f"{d[0:4]}-{d[4:8]}-{d[8:12]}"


def _ip(text: str):
    try:
        return ipaddress.ip_address(text.strip())
    except ValueError:
        return None


@dataclass
class End:
    query: str
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    mac: str | None = None
    segment: dict | None = None
    service: dict | None = None
    gateway: bool = False                 # the segment's own gateway address
    switch: str | None = None
    port: str | None = None
    found: list[str] = field(default_factory=list)   # where each fact came from

    def view(self) -> dict:
        return {"query": self.query, "ip": str(self.ip) if self.ip else None, "mac": self.mac,
                "segment": self.segment["name"] if self.segment else None,
                "service": self.service["name"] if self.service else None, "gateway": self.gateway,
                "switch": self.switch, "port": self.port, "found": self.found}


def _gateway(segment: dict, version: int) -> str | None:
    """The segment's gateway address of one IP version, with its length."""
    routing = segment.get("routing") or {}
    return routing.get("gateway") if version == 4 else (routing.get("ipv6") or {}).get("gateway")


def _service_addresses(service: dict) -> list[str]:
    return [a for a in (service.get("address"), (service.get("ipv6") or {}).get("address")) if a]


def gateway_segments(fabric: Fabric, switch: str, version: int = 4) -> list[dict]:
    """The routed segments with a gateway of that IP version on the switch (where the platform puts
    their VPN instance: routing_intent.vpns_on)."""
    from orchestrator.routing_intent import segment_vpn, vpns_on

    gateways = set(fabric.gateways) if fabric.architecture == "centralized" else None
    services = [{**s, "devices": s.get("devices") or [s.get("device")]} for s in fabric.model.services]
    segments = [{"attachments": [], **s} for s in fabric.model.segments]
    here = vpns_on(switch, services, segments, gateways, set(fabric.borders), ipv6=version == 6)
    return [s for s in fabric.model.segments if s.get("routing") and s.get("bd_id") is not None
            and segment_vpn(s["name"], s["bd_id"]) in here and _gateway(s, version)]


def switch_end(fabric: Fabric, query: str, version: int = 4) -> End | None:
    """a switch as an end is its gateway in a segment, `<switch>/<segment>`,
    or the switch alone when it routes one segment. None: not a switch name. ValueError: a switch
    with no such gateway."""
    name, _, wanted = query.strip().partition("/")
    if name not in fabric.switches:
        return None
    found = gateway_segments(fabric, name, version)
    family = "IPv6 " if version == 6 else ""
    article = "an IPv6 " if version == 6 else "a "
    if not found:
        raise ValueError(f"{name} routes no segment with {article}gateway: it is reached through a segment's gateway")
    if wanted:
        segment = next((s for s in found if s["name"] == wanted), None)
        if segment is None:
            raise ValueError(f"{name} has no {family}gateway in segment {wanted!r}; it has: "
                             + ", ".join(s["name"] for s in found))
    elif len(found) == 1:
        segment = found[0]
    else:
        raise ValueError(f"{name} routes several segments: write {name}/<segment>, one of "
                         + ", ".join(s["name"] for s in found))
    ip = ipaddress.ip_interface(_gateway(segment, version)).ip
    return End(query, ip=ip, segment=segment, gateway=True, switch=name,
               found=[f"intent: gateway Vbdif{segment['bd_id']} ({ip}) of segment {segment['name']} on {name}"])


def version_of(fabric: Fabric, other: str | None) -> int:
    """The IP version of a switch end: the other end's when that is an IP address, else IPv4."""
    address = _ip(other or "")
    return address.version if address is not None else 4


def locate(fabric: Fabric, tables: Tables, query: str, other: str | None = None) -> End:
    """Where an IP or MAC is: its segment from intent, its MAC from ARP, its switch and port from
    the MAC tables (a local port wins over an entry learned from a VTEP). A switch name is its
    gateway in a segment (`switch_end`); `other` is the query's other end."""
    if _ip(query) is None and mac_of(query) is None:
        found = switch_end(fabric, query, version_of(fabric, other))
        if found is not None:
            return found
    end = End(query, ip=_ip(query), mac=None if _ip(query) else mac_of(query))
    segments = fabric.model.segments
    if end.ip is not None:
        for s in segments:
            net = _net(_gateway(s, end.ip.version))
            if net is not None and end.ip in net:
                end.segment = s
                end.gateway = ipaddress.ip_interface(_gateway(s, end.ip.version)).ip == end.ip
                end.found.append(f"intent: segment {s['name']} ({net})")
        for service in fabric.model.services:
            if any((net := _net(a)) is not None and end.ip in net for a in _service_addresses(service)):
                end.service = service
                end.found.append(f"intent: service {service['name']} ({net})")
        if end.segment is not None and not end.gateway:
            vpn = vpn_of(end.segment)
            for switch, entries in sorted(tables.arp.items()):
                hit = next((e for e in entries if _ip(e["ip"] or "") == end.ip and e.get("vpn") in (vpn, None)), None)
                if hit and hit.get("mac"):
                    end.mac = hit["mac"]
                    end.found.append(f"{'ARP' if end.ip.version == 4 else 'ND'} on {switch}: {end.ip} is {end.mac}")
                    break
    if end.mac is not None:
        local, remote = None, None
        for switch, entries in sorted(tables.macs.items()):
            for e in entries:
                if e["mac"] != end.mac:
                    continue
                if e.get("peer") or str(e.get("interface") or "").startswith("Nve"):
                    remote = remote or (switch, e)
                elif local is None:
                    local = (switch, e)
        if local is not None:
            switch, e = local
            end.switch, end.port = switch, e.get("interface")
            end.found.append(f"MAC table on {switch}: {end.mac} on {end.port} in BD {e.get('bd')}")
            if end.segment is None:
                end.segment = next((s for s in segments if s.get("bd_id") == e.get("bd")), None)
        elif remote is not None:
            switch, e = remote
            owner = next((s for s, vtep in sorted(fabric.vteps.items()) if vtep == e.get("peer")), None)
            end.switch = owner
            end.found.append(f"MAC table on {switch}: {end.mac} learned from VTEP {e.get('peer')}"
                             + (f" ({owner})" if owner else ""))
            if end.segment is None:
                end.segment = next((s for s in segments if s.get("bd_id") == e.get("bd")), None)
    return end


def _hop(kind: str, text: str, source: str, switch: str | None = None) -> dict:
    return {"kind": kind, "text": text, "from": source, "switch": switch}


def _vtep(fabric: Fabric, switch: str | None) -> str:
    return f" (VTEP {fabric.vteps[switch]})" if switch in fabric.vteps else ""


def _same_place(fabric: Fabric, a: str | None, b: str | None) -> bool:
    """Two switches behind one VTEP (an M-LAG pair) bridge locally."""
    return a == b or (a is not None and a in fabric.vteps and fabric.vteps.get(a) == fabric.vteps.get(b))


def _host_hop(end: End, role: str) -> dict:
    if end.gateway and end.switch and end.port is None and end.query != str(end.ip):   # a switch as an end
        return _hop("host", f"{role} {end.switch}'s gateway {end.ip} in segment {end.segment['name']}",
                    end.found[0], end.switch)
    what = end.query if end.mac is None or end.query == end.mac else f"{end.query} ({end.mac})"
    if end.switch and end.port:
        return _hop("host", f"{role} {what} on {end.switch} {end.port}", end.found[-1], end.switch)
    where = f"in segment {end.segment['name']}" if end.segment else "in no segment of this fabric"
    seen = f"; seen behind {end.switch}" if end.switch else ""
    return _hop("host", f"{role} {what} {where}: not found on any switch port{seen}",
                end.found[-1] if end.found else "no table or intent knows it", end.switch)


def _bridge(fabric: Fabric, segment: dict, a: str | None, b: str | None) -> list[dict]:
    source = f"intent: segment {segment['name']} (VNI {segment['vni']}, BD {segment['bd_id']})"
    if a is None or b is None:
        return [_hop("vxlan", f"flooded in BD {segment['bd_id']} to every VTEP of {segment['name']}", source)]
    if _same_place(fabric, a, b):
        return [_hop("bridge", f"bridged in BD {segment['bd_id']} on {a}", source, a)]
    return [_hop("vxlan", f"VXLAN VNI {segment['vni']} from {a}{_vtep(fabric, a)} to {b}{_vtep(fabric, b)}", source, b)]


def _statics(fabric: Fabric, switch: str | None) -> list[dict]:
    targets = (fabric.model.routing or {}).get("devices") or {}
    for target, routing in sorted(targets.items()):
        if switch and switch in target.split("+"):
            return (routing or {}).get("static_routes") or []
    return []


def trace(fabric: Fabric, tables: Tables, source: str, destination: str) -> dict:
    src, dst = locate(fabric, tables, source, destination), locate(fabric, tables, destination, source)
    hops = [_host_hop(src, "from")]
    result = _path(fabric, src, dst, hops)
    return {"source": src.view(), "destination": dst.view(), "hops": hops, "result": result,
            "switches": list(dict.fromkeys(h["switch"] for h in hops if h["switch"]))}


def _path(fabric: Fabric, src: End, dst: End, hops: list[dict]) -> str:
    if src.segment is None:
        hops.append(_hop("drop", "the source is in no segment of this fabric: nothing to follow", "intent"))
        return UNKNOWN
    seg = src.segment
    centralized = fabric.architecture == "centralized"
    gateway_at = fabric.gateways if centralized else ([src.switch] if src.switch else [])
    if centralized and src.gateway and src.switch in fabric.gateways:
        gateway_at = [src.switch]   # a gateway switch as the source routes it itself
    gw_name = "+".join(gateway_at) if gateway_at else f"the leaves of {seg['name']}"

    if dst.segment is seg and not dst.gateway:   # one subnet: hosts reach each other bridged
        hops += _bridge(fabric, seg, src.switch, dst.switch)
        hops.append(_host_hop(dst, "to"))
        return DELIVERED if dst.port else UNKNOWN

    routing = seg.get("routing")
    if not routing:
        hops.append(_hop("drop", f"segment {seg['name']} is not routed: the destination is not in it",
                         f"intent: segment {seg['name']}"))
        return DROP
    if dst.ip is None:
        hops.append(_hop("drop", "the destination is a MAC outside the source's segment: routing needs its IP", "input"))
        return UNKNOWN
    vpn = vpn_of(seg)
    if centralized and src.switch and src.switch not in fabric.gateways:
        hops += _bridge(fabric, seg, src.switch, fabric.gateways[0] if fabric.gateways else None)
    version = dst.ip.version
    if _gateway(seg, version) is None:
        hops.append(_hop("drop", f"segment {seg['name']} has no IPv6: nothing routes this address",
                         f"intent: segment {seg['name']}"))
        return DROP
    gw_ip = str(ipaddress.ip_interface(_gateway(seg, version)).ip)
    hops.append(_hop("gateway", f"gateway Vbdif{seg['bd_id']} ({gw_ip}) on {gw_name} in {vpn}",
                     f"intent: segment {seg['name']} routing", gateway_at[0] if gateway_at else None))
    walked = {"switch": gateway_at[0] if gateway_at else None, "address": str(dst.ip)}
    if dst.gateway and dst.segment is seg:
        if dst.switch and gateway_at and dst.switch not in gateway_at:   # anycast: the nearest gateway answers
            hops.append(_hop("gateway", f"the gateway address is anycast: {gw_name} answers, not {dst.switch}",
                             f"intent: segment {seg['name']} routing", gateway_at[0]))
        return GATEWAY

    statics = _statics(fabric, gateway_at[0] if gateway_at else None)
    tables = _tables(fabric.model, statics)
    # Services by VPN instance: several may share one, or sit in a segment's.
    services: dict[str, list[dict]] = {}
    for s in fabric.model.services:
        services.setdefault(s.get("vrf") or s["name"], []).append(s)
    by_vpn = {vpn_of(s): s for s in fabric.model.segments if s.get("routing")}

    def firewall_in(target: str, came_from: str) -> dict:
        """The firewall a segment's default route reaches in `target`: the segment's own, by name."""
        name = ((by_vpn.get(came_from) or {}).get("routing") or {}).get("firewall")
        return next((s for s in services[target] if s["name"] == name), services[target][0])
    address = dst.ip
    for _ in range(16):
        matches = [r for r in tables.get(vpn, []) if address in r[0]]
        if not matches:
            hops.append(_hop("drop", f"no route to {address} in {vpn}: dropped", f"route model of {vpn}"))
            return DROP
        net, kind, target, label = max(matches, key=lambda r: r[0].prefixlen)
        hops[-1].setdefault("routes", []).append({**walked, "vpn": vpn, "prefix": str(net), "kind": kind})
        if kind == "null":
            hops.append(_hop("drop", f"{label}: discarded", "routing intent"))
            return DROP
        if kind == "nh":
            owner = next((s for s in fabric.model.services
                          if any((n := _net(a)) and target in n for a in _service_addresses(s))), None)
            hops.append(_hop("route", label, "routing intent"))
            if owner is not None:
                return _service_end(fabric, owner, str(target), hops, vpn)
            address = target
            continue
        if kind == "vpn":
            hops.append(_hop("route", label, "intent: " + ("firewall default" if target in services and target not in by_vpn else "segment peering")))
            if target in services and target not in by_vpn:
                service = firewall_in(target, vpn)
                return _service_end(fabric, service, service.get("next_hop"), hops, vpn)
            vpn = target
            if not centralized:   # the next instance is the far leaf's (symmetric: L3 VNI), not this switch's
                walked["switch"] = dst.switch
            continue
        # local: the destination's own subnet in this instance
        owner = next((s for s in services.get(vpn, [])
                      if any((n := _net(a)) and address in n for a in _service_addresses(s))), None)
        if owner is not None or (vpn in services and vpn not in by_vpn):
            return _service_end(fabric, owner or services[vpn][0], str(address), hops, vpn)
        far = by_vpn.get(vpn)
        if far is None:
            hops.append(_hop("drop", f"{address} is local to {vpn}, which no segment owns", "route model"))
            return UNKNOWN
        if dst.gateway:
            anycast = f" (anycast: the nearest switch with it answers, maybe not {dst.switch})" if dst.switch else ""
            hops.append(_hop("gateway", f"gateway Vbdif{far['bd_id']} of {far['name']}{anycast}", f"intent: segment {far['name']}"))
            return GATEWAY
        if centralized:
            hops.append(_hop("gateway", f"Vbdif{far['bd_id']} on {gw_name} into segment {far['name']}",
                             f"intent: segment {far['name']} routing", gateway_at[0] if gateway_at else None))
            hops += _bridge(fabric, far, fabric.gateways[0] if fabric.gateways else None, dst.switch)
        else:
            here = gateway_at[0] if gateway_at else None
            if dst.switch and here and not _same_place(fabric, here, dst.switch):
                hops.append(_hop("vxlan", f"L3 VNI {far['vni'] + L3_VNI_OFFSET} of {vpn} from {here}{_vtep(fabric, here)} "
                                 f"to {dst.switch}{_vtep(fabric, dst.switch)}", f"intent: segment {far['name']} routing", dst.switch))
            hops.append(_hop("gateway", f"Vbdif{far['bd_id']} on {dst.switch or 'the leaves of ' + far['name']} into BD {far['bd_id']}",
                             f"intent: segment {far['name']}", dst.switch))
        hops.append(_host_hop(dst, "to"))
        return DELIVERED if dst.port else UNKNOWN
    hops.append(_hop("drop", "more than 16 routing steps: a loop (the pre-deploy checks would show it)", "route model"))
    return DROP


def _service_end(fabric: Fabric, service: dict, next_hop: str | None, hops: list[dict], vpn: str) -> str:
    leaves = service.get("devices") or [service.get("device")]
    port = f" {service['port']}" if service.get("port") else ""   # an imported fabric's services ride VLAN trunks: no port of their own
    where = f"{'+'.join(leaves)}{port} (VLAN {service.get('vlan')})"
    kind = service.get("kind", "firewall")
    here = next((h["switch"] for h in reversed(hops) if h["switch"]), None)
    if fabric.architecture != "centralized" and here and not set(leaves) & {here} and \
            not any(_same_place(fabric, here, leaf) for leaf in leaves):
        own = next((x for x in fabric.model.segments if x.get("bd_id") is not None
                    and vpn_of(x) == vpn), None)
        carrier = f"L3 VNI {own['vni'] + L3_VNI_OFFSET} of {vpn}" if own else f"routed in {vpn}"
        hops.append(_hop("vxlan", f"{carrier} from {here}{_vtep(fabric, here)} to {'+'.join(leaves)}{_vtep(fabric, leaves[0])}, "
                         "the service's leaf",
                         f"intent: service {service['name']}", leaves[0]))
    if kind == "isp":
        hops.append(_hop("end", f"ISP router {next_hop} behind service {service['name']} on {where}: the path leaves the fabric",
                         f"intent: service {service['name']}", leaves[0]))
        return ISP
    hops.append(_hop("end", f"firewall {next_hop} behind service {service['name']} on {where}: the path ends at the firewall "
                     "(its inside is unknown)", f"intent: service {service['name']}", leaves[0]))
    return FIREWALL


# -- reading the switches (on demand, nothing stored) ----------------------

ARP_NS, MAC_NS, ND_NS = "urn:huawei:yang:huawei-arp", "urn:huawei:yang:huawei-mac", "urn:huawei:yang:huawei-ipv6-nd"
# One subtree filter per table: ncclient parses a filter as one XML document (one root element).
ARP_FILTER = (f'<arp xmlns="{ARP_NS}"><query-entries><query-entry><ni-name/><ip-addr/><mac-addr/><style-type/><if-name/>'
              '</query-entry></query-entries></arp>')
MAC_FILTER = (f'<mac xmlns="{MAC_NS}"><bd-dynamic-macs><bd-dynamic-mac><address/><bd-id/><type/><out-interface-name/>'
              '<peer-ip/></bd-dynamic-mac></bd-dynamic-macs></mac>')
ND_FILTER = (f'<ipv6-nd xmlns="{ND_NS}"><ipv6-neighbors><ipv6-neighbor><if-name/><ipv6-addr/><mac-addr/><vpn-name/><state/>'
             '</ipv6-neighbor></ipv6-neighbors></ipv6-nd>')   # valid and empty, entries unseen


def _children(element, ns: str) -> dict[str, str]:
    return {child.tag.split("}", 1)[1]: (child.text or "").strip() for child in element if child.tag.startswith(f"{{{ns}}}")}


def parse_tables(*replies: str) -> tuple[list[dict], list[dict]]:
    """ARP entries and MAC entries of one switch's replies to ARP_FILTER and MAC_FILTER."""
    import xml.etree.ElementTree as ET

    root = ET.Element("all")
    root.extend(ET.fromstring(reply) for reply in replies)
    arp = [{"vpn": e.get("ni-name"), "ip": e.get("ip-addr"), "mac": e.get("mac-addr"), "type": e.get("style-type"),
            "interface": e.get("if-name")}
           for e in (_children(x, ARP_NS) for x in root.iter(f"{{{ARP_NS}}}query-entry"))]
    arp += [{"vpn": e.get("vpn-name") or None, "ip": e.get("ipv6-addr"), "mac": e.get("mac-addr"), "type": "nd",
             "interface": e.get("if-name")}
            for e in (_children(x, ND_NS) for x in root.iter(f"{{{ND_NS}}}ipv6-neighbor"))]
    macs = [{"bd": int(e["bd-id"]) if e.get("bd-id", "").isdigit() else None, "mac": e.get("address"),
             "type": e.get("type"), "interface": e.get("out-interface-name") or None, "peer": e.get("peer-ip") or None}
            for e in (_children(x, MAC_NS) for x in root.iter(f"{{{MAC_NS}}}bd-dynamic-mac"))]
    return arp, macs


class TableReader:
    """ARP and MAC tables of the fabric's active, pinned switches, one NETCONF session at a time
    (rapid connections to port 830 are reset), with the collector's read account."""

    def __init__(self, route_reader):
        self.reader = route_reader   # orchestrator.routes.RouteReader: target checks, login, connect

    def _read(self, target) -> list[str]:
        session = self.reader._connect(target)
        try:
            return [session.get(filter=("subtree", f)).data_xml for f in (ARP_FILTER, MAC_FILTER, ND_FILTER)]
        finally:
            try:
                session.close_session()
            except Exception:   # the reply is already read
                pass

    async def read(self, switches: list[str]) -> tuple[Tables, list[str]]:
        """The tables, and a line per switch that could not be read (the trace goes on without it)."""
        import asyncio

        tables, problems = Tables(), []
        bound = asyncio.Semaphore(PARALLEL)   # one session per switch: only one switch's port 830 resets rapid ones

        async def one(name: str) -> None:
            try:
                async with bound:
                    target = await self.reader.target(name)
                    arp, macs = parse_tables(*await asyncio.to_thread(self._read, target))
            except Exception as exc:
                problems.append(f"{name}: {exc}")
                return
            tables.arp[name], tables.macs[name] = arp, macs

        await asyncio.gather(*(one(name) for name in switches))
        return tables, problems


async def load(nb, fabric: str) -> tuple[Fabric, list[str]]:
    """The fabric's intended model for a trace, and its active switches."""
    import asyncio

    from orchestrator.routing_intent import RoutingStore, status_of

    store = RoutingStore(nb)
    (services, segments), document, gateways, devices, kept = await asyncio.gather(
        store.fabric_state(fabric), store.load(fabric), store.gateways(fabric), nb.find("dcim/devices", site=fabric),
        documents.find(nb, f"overrides-{fabric}"))
    # an imported fabric's own names
    scheme = (((kept or {}).get("data") or {}).get("overrides") or {}).get("segments") or {}
    segments = [{**s, **({"vrf": v} if (v := (scheme.get(str(s["vni"])) or {}).get("vrf")) else {})} for s in segments]
    active = sorted(d["name"] for d in devices if status_of(d) == "active")
    borders = sorted(d["name"] for d in devices if (d.get("role") or {}).get("slug") == "border-leaf")
    addresses = await asyncio.gather(*(nb.find("ipam/ip-addresses", device_id=d["id"])
                                       for d in devices if d["name"] in active))
    vteps = {}
    for found in addresses:
        for a in found:
            assigned = a.get("assigned_object") or {}
            role = a.get("role")
            role = role.get("value") if isinstance(role, dict) else role
            if role == "anycast" and (assigned.get("device") or {}).get("name"):   # the VTEP (LoopBack1, or 2)
                vteps[assigned["device"]["name"]] = a["address"].split("/")[0]
    model = Model(segments=segments, services=services, routing=document.model_dump(mode="json", exclude_defaults=True),
                  pools={}, planned=set(), ports_down=set(), mlag_trunks=set(), peers={})
    return (Fabric(model, "centralized" if gateways is not None else "symmetric", sorted(gateways or ()), vteps,
                   sorted(d["name"] for d in devices), borders if gateways is None else []), active)


def check_ends(fabric: Fabric, source: str, destination: str) -> None:
    """ValueError when an end is neither an IP, a MAC nor a switch with a gateway."""
    for value, other in ((source, destination), (destination, source)):
        if _ip(value) is None and mac_of(value) is None and switch_end(fabric, value, version_of(fabric, other)) is None:
            raise ValueError(f"{value!r} is neither an IP address, a MAC address nor a switch of this fabric")


async def run(nb, reader: TableReader, fabric: str, source: str, destination: str) -> dict:
    model, active = await load(nb, fabric)
    check_ends(model, source, destination)
    tables, problems = await reader.read(active)
    return {"fabric": fabric, "architecture": model.architecture, **trace(model, tables, source, destination),
            "read": sorted(tables.arp), "unread": problems}
