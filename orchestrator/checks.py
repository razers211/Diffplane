"""Pre-deploy checks: problems in the intended model that a diff cannot show.

Errors block approval; warnings are shown next to the patch and the approver decides.

Errors:
- route cycle: following the routes of a VPN instance comes back to a route already followed (the
  platform's inter-VPN routes for firewall services and segment peering, and routing-intent static
  routes resolved through their next hop, per routing target);
- duplicate addresses: segment gateway subnets and service subnets overlapping one another;
- pool overlap: a gateway or service subnet inside the loopback, VTEP, point-to-point or bypass pool.

Warnings:
- a routed segment attached nowhere (its gateway serves no port);
- a service on a planned switch, or on a port the collector reports down;
- a BGP peer in an ISP service without an export policy or prefix list;
- a segment on an M-LAG trunk of one leaf but not on the same trunk of its peer.

Port layouts (every active switch): an error while a switch reports another model than NetBox's,
or its front-panel ports in NetBox differ from those the collector reads, or a model whose ports
are only assumed (seed.ports.ASSUMED, never read from a switch) has not been read yet: nothing is
deployed to ports that may not exist. Reading the switch (its Settings tab, after a pin, or the
hourly sweep) brings NetBox in step unless a port in use would vanish.

Microsegmentation, while the fabric's mode is Learn or Enforce:
- error: the intent needs more groups, members or rules than one chip holds;
- warning (Enforce): two segments that route to each other (peered, or behind the same firewall)
  with no contract between their groups either way: their traffic is dropped;
- warning: a contract with an end whose segment is not routed (it is on no switch);
- warning: a named group left with no member (its prefixes outside its segment's subnet now).
"""

import asyncio
import ipaddress
import re
from dataclasses import dataclass, field

from generators.fabric import segment_vpn
from seed import ports

ERROR, WARNING = "error", "warning"
GLOBAL = "_public_"
SMALL_POOLS = ("loopbacks", "VTEP anycast loopbacks", "spine-leaf point-to-point links",
               "M-LAG bypass links over the peer-link", "VXLAN PIP loopbacks")   # seed.loader.POOLS purposes, gateways aside


def finding(severity: str, check: str, text: str, *objects: str) -> dict:
    return {"severity": severity, "check": check, "text": text, "objects": list(objects)}


@dataclass
class Model:
    segments: list[dict]                 # Segments.all(...) of the fabric
    services: list[dict]                 # Routing.services(...)
    routing: dict                        # routing document: {"devices": {target: {...}}}
    pools: dict[str, ipaddress.IPv4Network]          # purpose -> pool (non-gateway pools)
    planned: set[str]                    # planned switches
    ports_down: set[tuple[str, str]]     # (switch, port) the collector reports down
    mlag_trunks: set[tuple[str, str]]    # (switch, Eth-TrunkN) tagged m-lag
    peers: dict[str, str]                # switch -> M-LAG peer
    microseg: dict = field(default_factory=dict)          # the fabric's microsegmentation document
    routed: dict[int, dict] = field(default_factory=dict)  # routed segments as published, by L2 VNI
    # active switches of an unverified model: (model, NetBox front-panel ports, ports read or None)
    # switch -> (NetBox model, NetBox's front-panel ports, the collector's or None, model the switch reports or None)
    layouts: dict[str, tuple[str, set[str], set[str] | None, str | None]] = field(default_factory=dict)
    ports_off: set[tuple[str, str]] = field(default_factory=set)   # (switch, port) turned off, while managed


def _net(address: str | None):
    try:
        return ipaddress.ip_interface(address).network if address else None
    except ValueError:
        return None


def vpn_of(segment: dict) -> str:
    """The segment's VPN instance: the one an import kept (`vrf`, set by trace.load), else derived."""
    return segment.get("vrf") or segment_vpn(segment["name"], segment["bd_id"])


def service_vpn(service: dict) -> str:
    """The service's VPN instance: its own, or the one it sits in."""
    return service.get("vrf") or service["name"]


def _nets6(segment: dict) -> list:
    """The segment's IPv6 subnet, when it has one."""
    net = _net(((segment.get("routing") or {}).get("ipv6") or {}).get("gateway"))
    return [net] if net is not None else []


def _routed(model: Model) -> list[tuple[dict, str, ipaddress.IPv4Network]]:
    return [(s, vpn_of(s), _net(s["routing"]["gateway"]))
            for s in model.segments if s.get("routing") and s.get("bd_id") is not None]


# -- route cycles -----------------------------------------------------------------------------------

def _tables(model: Model, statics: list[dict]) -> dict[str, list[tuple]]:
    """VPN instance -> [(network, kind, target, label)]: kind is local, vpn (to another instance),
    nh (a next hop resolved in the same instance) or null."""
    tables: dict[str, list[tuple]] = {}
    routed = _routed(model)
    by_vni = {s["vni"]: (vpn, [net, *_nets6(s)]) for s, vpn, net in routed}
    # Keyed by VPN instance: services may share one, or sit in a segment's.
    firewalls = {s["name"]: s for s in model.services if s.get("kind", "firewall") == "firewall"}
    with_ipv6 = {s["name"] for s in model.services if (s.get("ipv6") or {}).get("address")}
    for service in model.services:
        for address in (service.get("address"), (service.get("ipv6") or {}).get("address")):
            if (net := _net(address)) is not None:
                tables.setdefault(service_vpn(service), []).append((net, "local", None, f"{service['name']} subnet"))
    for segment, vpn, net in routed:
        own = [net, *_nets6(segment)]
        tables.setdefault(vpn, []).extend((n, "local", None, f"{vpn} gateway") for n in own)
        firewall = segment["routing"].get("firewall")
        if firewall in firewalls:
            target = service_vpn(firewalls[firewall])
            six = len(own) > 1 and firewall in with_ipv6
            if target == vpn:   # the firewall in the segment's own instance: a default via its address
                hops = [firewalls[firewall]["next_hop"], *([firewalls[firewall]["ipv6"]["next_hop"]] if six else [])]
                tables[vpn].extend((ipaddress.ip_network("::/0" if ":" in h else "0.0.0.0/0"), "nh", ipaddress.ip_address(h),
                                    f"{vpn} default via {firewall}") for h in hops)
                continue
            tables[vpn].append((ipaddress.ip_network("0.0.0.0/0"), "vpn", target, f"{vpn} default via {firewall}"))
            if six:
                tables[vpn].append((ipaddress.ip_network("::/0"), "vpn", target, f"{vpn} IPv6 default via {firewall}"))
            tables.setdefault(target, []).extend((n, "vpn", vpn, f"{firewall} route to {n} in {vpn}") for n in own[:1 + six])
        for peer in segment["routing"].get("peers") or []:
            if peer in by_vni:
                peer_vpn, peer_nets = by_vni[peer]
                # IPv6 only between two dual-stack segments (generators: both ends need the family)
                tables[vpn].extend((n, "vpn", peer_vpn, f"{vpn} route to {n} in {peer_vpn}")
                                   for n in peer_nets if n.version == 4 or len(own) > 1)
    for route in statics:
        vpn = route.get("vpn") or GLOBAL
        net = ipaddress.ip_network(route["prefix"])
        hop = route["next_hop"]
        kind, target = ("null", None) if hop == "NULL0" else ("nh", ipaddress.ip_address(hop))
        tables.setdefault(vpn, []).append((net, kind, target, f"static {net} via {hop}" + (f" in {vpn}" if vpn != GLOBAL else "")))
    return tables


def _cycle(tables: dict[str, list[tuple]], vpn: str, address) -> list[str] | None:
    """Follow the longest match for `address` from `vpn`; the routes walked if one repeats."""
    walked: list[tuple] = []
    for _ in range(64):
        matches = [r for r in tables.get(vpn, []) if address in r[0]]
        if not matches:
            return None
        route = max(matches, key=lambda r: r[0].prefixlen)
        if (vpn, route) in walked:
            return [r[3] for _, r in walked[walked.index((vpn, route)):]] + [route[3]]
        walked.append((vpn, route))
        if route[1] == "vpn":
            vpn = route[2]
        elif route[1] == "nh":
            address = route[2]
        else:
            return None
    return [r[3] for _, r in walked]


def route_cycles(model: Model) -> list[dict]:
    found, seen = [], set()
    # Per switch: its pair's static routes and its own meet there.
    by_switch: dict[str, list] = {}
    for target, routing in ((model.routing or {}).get("devices") or {}).items():
        for switch in target.split("+"):
            by_switch.setdefault(switch, []).extend((routing or {}).get("static_routes") or [])
    for target, routes in sorted((by_switch or {"": []}).items()):
        tables = _tables(model, routes)
        for vpn, routes in sorted(tables.items()):
            for route in routes:
                loop = _cycle(tables, vpn, route[0].network_address)
                if loop and frozenset(loop) not in seen:
                    seen.add(frozenset(loop))
                    where = f" on {target}" if target else ""
                    found.append(finding(ERROR, "route-cycle", f"routing loop{where}: " + " → ".join(loop), vpn))
    return found


# -- addresses ------------------------------------------------------------------------------------

def _subnets(model: Model) -> list[tuple[str, ipaddress.IPv4Network]]:
    subnets = [(f"segment {s['name']} gateway {net}", net) for s, _, net in _routed(model) if net is not None]
    subnets += [(f"service {s['name']} subnet {net}", net) for s in model.services if (net := _net(s.get("address")))]
    return subnets


def duplicate_addresses(model: Model) -> list[dict]:
    subnets, found = _subnets(model), []
    for i, (label, net) in enumerate(subnets):
        for other, other_net in subnets[i + 1:]:
            if net.overlaps(other_net):
                found.append(finding(ERROR, "duplicate-address", f"{label} overlaps {other}", label, other))
    return found


def pool_overlaps(model: Model) -> list[dict]:
    return [finding(ERROR, "pool-overlap", f"{label} overlaps the fabric's {purpose} pool {pool}", label)
            for label, net in _subnets(model) for purpose, pool in sorted(model.pools.items()) if net.overlaps(pool)]


# -- warnings -------------------------------------------------------------------------------------

def unattached_segments(model: Model) -> list[dict]:
    return [finding(WARNING, "unattached", f"segment {s['name']} is routed but attached to no port: its gateway "
                    "serves nobody", s["name"]) for s, _, _ in _routed(model) if not s["attachments"]]


def service_ports(model: Model) -> list[dict]:
    found = []
    for service in model.services:
        leaves = [service["device"]] + ([model.peers[service["device"]]]
                                        if service.get("addresses") and service["device"] in model.peers else [])
        for leaf in leaves:
            if leaf in model.planned:
                found.append(finding(WARNING, "service-planned", f"service {service['name']} is on {leaf}, which is "
                                     "planned: nothing is deployed there", service["name"], leaf))
            elif (leaf, service["port"]) in model.ports_down:
                found.append(finding(WARNING, "service-down", f"service {service['name']}: {leaf} {service['port']} is "
                                     "down, the firewall or ISP cannot be reached", service["name"], leaf))
    return found


def isp_peers_without_export(model: Model) -> list[dict]:
    isps = {service_vpn(s) for s in model.services if s.get("kind") == "isp"}
    found = []
    for target, routing in sorted(((model.routing or {}).get("devices") or {}).items()):
        for peer in (routing or {}).get("bgp_peers") or []:
            if peer.get("vpn") in isps and not (peer.get("export_policy") or peer.get("export_prefix_list")):
                found.append(finding(WARNING, "isp-export", f"BGP peer {peer['address']} of ISP service {peer['vpn']} on "
                                     f"{target} has no export policy or prefix list", target))
    return found


def one_sided_mlag(model: Model) -> list[dict]:
    found = []
    attached = {(a["device"], a["interface"], s["vni"]) for s in model.segments for a in s["attachments"]}
    for device, interface, vni in sorted(attached):
        peer = model.peers.get(device)
        if (device, interface) in model.mlag_trunks and peer and (peer, interface, vni) not in attached:
            name = next(s["name"] for s in model.segments if s["vni"] == vni)
            found.append(finding(WARNING, "one-sided-mlag", f"segment {name} is on M-LAG trunk {interface} of {device} "
                                 f"but not of {peer}", device, peer))
    return found


def ports_off_in_use(model: Model) -> list[dict]:
    """a port or trunk turned off that carries segments or a service: they stop there."""
    found = []
    for device, port in sorted(model.ports_off):
        what = [s["name"] for s in model.segments for a in s["attachments"] if (a["device"], a["interface"]) == (device, port)]
        what += [f"service {s['name']}" for s in model.services if s["port"] == port and (
            s["device"] == device or (s.get("addresses") and model.peers.get(s["device"]) == device))]
        if what:
            found.append(finding(WARNING, "port-off-in-use", f"{device} {port} is turned off: {', '.join(what)} "
                                 "stop there", device))
    return found


# -- microsegmentation ------------------------------------------------------------------------

def microseg_checks(model: Model) -> list[dict]:
    from orchestrator import microseg

    doc = {**microseg.empty(), **(model.microseg or {})}
    if doc["mode"] == "off":
        return []
    found, routed, epgs = [], model.routed, doc["epgs"]
    usage = microseg.estimate(doc, routed)
    for kind, limit in microseg.LIMITS.items():
        if usage[kind] > limit:
            found.append(finding(ERROR, "microseg-limit", f"microsegmentation needs {usage[kind]} {kind} on a switch; "
                                 f"one chip holds {limit}"))
    name = lambda key: microseg.epg_name(epgs[str(key)], routed) if str(key) in epgs else None
    for c in doc["contracts"]:
        ends = [name(c["from"]), name(c["to"])]
        if None in ends:
            found.append(finding(WARNING, "microseg-inactive", f"contract EPG {c['from']} -> EPG {c['to']}: an end's segment "
                                 "is not routed, so it is on no switch"))
    for key, epg in sorted(epgs.items(), key=lambda i: int(i[0])):
        vni = int(epg["segment"])
        if not epg.get("name") or vni not in routed:
            continue
        nets = microseg.segment_networks(routed[vni])
        live = [p for p in epg.get("prefixes") or () if (n := ipaddress.ip_network(p)) not in nets
                and any(n.version == s.version and n.subnet_of(s) for s in nets)]
        if not live and not epg.get("macs"):
            found.append(finding(WARNING, "microseg-empty", f"group {name(key)} has no member left: its prefixes are not "
                                 f"inside segment {routed[vni]['name']}'s subnets any more", name(key)))
    if doc["mode"] == "enforce":
        found += _unprotected(doc, routed)
    return found


def _unprotected(doc: dict, routed: dict[int, dict]) -> list[dict]:
    """Routed segment pairs that reach each other with no contract between their EPGs (a
    segment's own or its groups') in either direction."""
    of_segment: dict[int, set[int]] = {}
    for key, epg in doc["epgs"].items():
        of_segment.setdefault(int(epg["segment"]), set()).add(int(key))
    allowed = {(c["from"], c["to"]) for c in doc["contracts"]}
    found = []
    vnis = sorted(routed)
    for i, a in enumerate(vnis):
        for b in vnis[i + 1:]:
            ra, rb = routed[a]["routing"], routed[b]["routing"]
            peered = b in (ra.get("peers") or ()) or a in (rb.get("peers") or ())
            firewall = ra.get("firewall") and ra.get("firewall") == rb.get("firewall")
            if not (peered or firewall):
                continue
            ea, eb = of_segment.get(a, set()), of_segment.get(b, set())
            if not any((x, y) in allowed or (y, x) in allowed for x in ea for y in eb):
                how = "are peered" if peered else f"share firewall {ra['firewall']}"
                found.append(finding(WARNING, "microseg-no-contract", f"segments {routed[a]['name']} and {routed[b]['name']} "
                                     f"{how} but no contract allows either to reach the other: Enforce drops it",
                                     routed[a]["name"], routed[b]["name"]))
    return found


def port_layouts(model: Model) -> list[dict]:
    found = []
    for switch, (name, ours, seen, reported) in sorted(model.layouts.items()):
        if reported and reported != name:
            found.append(finding(ERROR, "port-layout", f"{switch}: the switch reports model {reported}, NetBox has {name}; "
                                 "read it from its Settings tab (a port in use it does not have keeps NetBox as is)", switch))
            continue
        if seen is None:
            if name in ports.ASSUMED and name not in ports.LEARNED:
                found.append(finding(ERROR, "port-layout", f"{switch} ({name}): its port names are assumed and the "
                                     "collector has not read its interfaces yet; deploy once it has", switch))
            continue
        missing, extra = sorted(ours - seen, key=_port_order), sorted(seen - ours, key=_port_order)
        if missing or extra:
            parts = ([f"not on the switch: {', '.join(missing[:6])}{' ...' if len(missing) > 6 else ''}"] if missing else []) + \
                    ([f"not in the model: {', '.join(extra[:6])}{' ...' if len(extra) > 6 else ''}"] if extra else [])
            found.append(finding(ERROR, "port-layout", f"{switch} ({name}): NetBox's ports differ from the switch's "
                                 f"({'; '.join(parts)}); read it from its Settings tab", switch))
    return found


def _port_order(name: str) -> tuple:
    head, _, rest = name.partition("GE")
    return (head, *(int(n) for n in rest.split("/") if n.isdigit()))


FRONT_PANEL = re.compile(r"[A-Za-z0-9]*GE\d+/\d+/\d+")


CHECKS = (route_cycles, duplicate_addresses, pool_overlaps, unattached_segments, service_ports,
          isp_peers_without_export, one_sided_mlag, microseg_checks, port_layouts, ports_off_in_use)


def run(model: Model) -> list[dict]:
    found = [f for check in CHECKS for f in check(model)]
    return sorted(found, key=lambda f: (f["severity"] != ERROR, f["check"], f["text"]))


# -- from NetBox and Prometheus -------------------------------------------------------------------

async def check_fabric(nb, prometheus, fabric: str) -> dict:
    """The fabric's findings now, and whether any blocks approval."""
    from orchestrator.routing_intent import RoutingStore, status_of
    from orchestrator.segments import MLAG_TAG, _cf, _ref_id
    from orchestrator.trunks import _tags

    store = RoutingStore(nb)
    (services, segments), document, site, devices = await asyncio.gather(
        store.fabric_state(fabric), store.load(fabric), nb.find_one("dcim/sites", slug=fabric),
        nb.find("dcim/devices", site=fabric))
    vrf, interfaces = await asyncio.gather(nb.find_one("ipam/vrfs", name=f"fabric-{fabric}"),
                                           nb.find("dcim/interfaces", site=fabric))
    prefixes = await nb.find("ipam/prefixes", vrf_id=vrf["id"]) if vrf else []
    pools = {purpose: ipaddress.ip_network(p["prefix"]) for p in prefixes for purpose in SMALL_POOLS
             if p.get("description") == f"{(site or {}).get('name')} {purpose}"}
    names = {d["id"]: d["name"] for d in devices}
    peers = {d["name"]: names[_ref_id(_cf(d, "mlag_peer"))] for d in devices if _ref_id(_cf(d, "mlag_peer")) in names}
    ports_down: set[tuple[str, str]] = set()
    link_state = "unknown"
    if prometheus is not None:
        try:
            for service in services:
                for leaf in service["devices"]:
                    _, _, oper = await prometheus.device_state(leaf)
                    if oper.get(service["port"]) is False:
                        ports_down.add((leaf, service["port"]))
            link_state = "read"
        except Exception:   # the checks still run; port state is then unknown and said so
            link_state = "unknown"
    reported = {}
    if prometheus is not None:
        try:
            reported = {labels["device"]: labels.get("model") for labels, _, _ in await prometheus.query("fabric_device_info")}
        except Exception:   # not read: models are then not compared
            reported = {}
    await ports.refresh(nb)
    layouts = {}
    for d in devices:
        name = (d.get("device_type") or {}).get("model")
        if status_of(d) != "active":
            continue
        ours = {i["name"] for i in interfaces if i["device"]["name"] == d["name"] and FRONT_PANEL.fullmatch(i["name"])}
        seen = None
        if prometheus is not None:
            try:
                _, _, oper = await prometheus.device_state(d["name"])
                seen = {n for n in oper if FRONT_PANEL.fullmatch(n)} or None
            except Exception:   # not read: the check says so and blocks
                seen = None
        layouts[d["name"]] = (name, ours, seen, reported.get(d["name"]))
    from orchestrator import microseg

    from orchestrator import port_settings

    doc, routed, managed = await asyncio.gather(microseg.document(nb, fabric), microseg._routed(nb, fabric),
                                                port_settings.document(nb, fabric))
    model = Model(
        ports_off={(i["device"]["name"], i["name"]) for i in interfaces if i.get("enabled") is False} if managed["enabled"] else set(),
        microseg=doc, routed=routed, layouts=layouts,
        segments=segments, services=services, routing=document.model_dump(mode="json", exclude_defaults=True),
        pools=pools, planned={d["name"] for d in devices if status_of(d) != "active"}, ports_down=ports_down,
        mlag_trunks={(i["device"]["name"], i["name"]) for i in interfaces if MLAG_TAG in _tags(i)}, peers=peers)
    found = run(model)
    return {"fabric": fabric, "findings": found, "blocking": any(f["severity"] == ERROR for f in found),
            "link_state": link_state}
