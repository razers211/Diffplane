"""Why can't A reach B?: one query, the first place traffic stops and why.

The path trace both ways, then what the trace cannot know: a host's port (link down, port protection
acting), the switch's real routing table against the intended route, microsegmentation between the
two ends on the protocol and port asked. Checks run in the traffic's order and the first failure is
the answer, with the page that fixes it. Nothing is stored or configured. `reach` is pure; `run`
reads the switches. Forwarding itself is unverified (one lab switch).
"""

import asyncio
import ipaddress
from dataclasses import dataclass, field

from generators.microsegmentation import classifier
from orchestrator import dci, microseg, trace
from orchestrator.annet import PARALLEL
from orchestrator.routes import parse_rib

PAGES = {"security": "#/network/security", "segments": "#/network/segments", "changes": "#/changes",
         "protection": "#/network/protection"}
PROTOCOLS = {"tcp": 6, "udp": 17, "icmp": 1}


@dataclass
class Facts:
    """What was read beside the ARP and MAC tables; a None or missing value means not known."""
    ports_up: dict[str, dict[str, bool]] = field(default_factory=dict)    # switch -> port -> oper up
    trips: dict[tuple[str, str], list[str]] = field(default_factory=dict) # (switch, port) -> causes
    ribs: dict[str, list[dict]] = field(default_factory=dict)             # switch -> parse_rib
    microseg: dict = field(default_factory=dict)                          # the fabric's document
    routed: dict[int, dict] = field(default_factory=dict)                 # routed segments by VNI
    failed_rules: dict[str, set[str]] = field(default_factory=dict)       # switch -> classifiers not installed
    stretched: dict[int, str] = field(default_factory=dict)               # VNI -> the other fabric
    unread: list[str] = field(default_factory=list)


def _check(name: str, ok: bool | None, text: str, source: str, switch: str | None = None, page: str | None = None) -> dict:
    """ok None: could not be checked (never an answer on its own)."""
    return {"check": name, "ok": ok, "text": text, "from": source, "switch": switch,
            "page": (f"#/switches/{switch}" if page == "switch" else PAGES.get(page or ""))}


def _base_port(port: str) -> str:
    return port.split(".", 1)[0]


def _port_checks(facts: Facts, end: trace.End) -> list[dict]:
    """The port a located host is on: up, and no protection acting on it."""
    if not (end.switch and end.port):
        return []
    port, known = _base_port(end.port), facts.ports_up.get(end.switch)
    where = f"{end.switch} {port}"
    source = "collector"
    found = []
    if trips := facts.trips.get((end.switch, port)):
        found.append(_check("protection", False, f"port protection acts on {where}: {', '.join(trips)}", source,
                            end.switch, "protection"))
    if known is None or port not in known:
        found.append(_check("port", None, f"state of {where} not known", source, end.switch))
    else:
        found.append(_check("port", known[port], f"{where} is {'up' if known[port] else 'down'}", source, end.switch, "switch"))
    return found


def _rib_checks(facts: Facts, hops: list[dict]) -> list[dict]:
    """Each route the walk took: the switch's real table must hold it, or something more specific."""
    found = []
    for route in (r for h in hops for r in h.get("routes", [])):
        switch, vpn, prefix = route["switch"], route["vpn"], route["prefix"]
        if switch is None or switch not in facts.ribs:
            found.append(_check("route", None, f"{switch or 'the switch'}'s routing table was not read", "not read", switch))
            continue
        net, address = ipaddress.ip_network(prefix), ipaddress.ip_address(route["address"])
        hit = None
        for r in facts.ribs[switch]:
            have = ipaddress.ip_network(r["prefix"], strict=False)
            if r["vpn"] == vpn and r["active"] and have.version == net.version and address in have \
                    and have.prefixlen >= net.prefixlen and (hit is None or have.prefixlen > hit[0].prefixlen):
                hit = (have, r)
        if hit is None:
            found.append(_check("route", False, f"{switch} has no route for {prefix} in {vpn}: not deployed, or out of sync",
                                f"routing table of {switch}", switch, "changes"))
        else:
            found.append(_check("route", True, f"{switch} routes {prefix} in {vpn} ({hit[1]['protocol']})",
                                f"routing table of {switch}", switch))
    return found


def _epg(facts: Facts, end: trace.End) -> tuple[str, dict] | None:
    """The end's group: a named group holding its MAC or the longest prefix over it, else its segment's."""
    if end.segment is None:
        return None
    best, whole = None, None
    for key, epg in (facts.microseg.get("epgs") or {}).items():
        if int(epg["segment"]) != end.segment["vni"]:
            continue
        if not epg.get("name"):
            whole = (key, epg)
        elif end.mac and end.mac in (epg.get("macs") or ()):
            return key, epg
        elif end.ip is not None:
            for prefix in epg.get("prefixes") or ():
                net = ipaddress.ip_network(prefix)
                if net.version == end.ip.version and end.ip in net and (best is None or net.prefixlen > best[0]):
                    best = (net.prefixlen, (key, epg))
    return best[1] if best else whole


def _service_matches(service: dict, proto: int | None, port: int | None) -> bool:
    wanted = service["protocol"]
    if wanted == "any" or proto is None:
        return True
    number = PROTOCOLS[wanted] if isinstance(wanted, str) else wanted
    if number != proto and not (number == 1 and proto == 58):
        return False
    return port is None or not service.get("ports") or service["ports"][0] <= port <= service["ports"][1]


def _microseg_checks(facts: Facts, src: trace.End, dst: trace.End, proto: int | None, port: int | None,
                     where: str | None) -> list[dict]:
    doc = facts.microseg or {}
    mode = doc.get("mode", "off")
    if mode == "off":
        return []
    source = "security groups"
    a, b = _epg(facts, src), _epg(facts, dst)
    if a is None or b is None:   # outside: hosts in no group are permitted
        return [_check("security", True, "one end is in no group: allowed", source, where)]
    (a_id, a_epg), (b_id, b_epg) = a, b
    name = lambda epg: microseg.epg_name(epg, facts.routed) or f"group of segment {epg['segment']}"
    if a_id == b_id:
        allowed = (a_epg.get("inside") or "permit") != "deny"
        return [_check("security", allowed, f"inside {name(a_epg)} traffic is {'allowed' if allowed else 'denied'}",
                       source, where, None if allowed else "security")]
    contracts = [c for c in doc.get("contracts") or [] if str(c["from"]) == a_id and str(c["to"]) == b_id]
    match = next((c for c in contracts if any(_service_matches(s, proto, port) for s in c["services"])), None)
    if match is None:
        name_of = {v: k.upper() for k, v in PROTOCOLS.items()}
        what = "this traffic" if proto is None else f"{name_of.get(proto, f'protocol {proto}')}" + (f" {port}" if port else "")
        text = f"no rule lets {name(a_epg)} reach {name(b_epg)} on {what}"
        if mode == "learn":   # Learn permits between groups: said, not a stop
            return [_check("security", True, f"{text}: Enforce would block it (Learn allows it now)", source, where)]
        return [_check("security", False, text, source, where, "security")]
    if where and classifier(match) in facts.failed_rules.get(where, ()):
        return [_check("security", False, f"the rule {name(a_epg)} to {name(b_epg)} is not installed on {where}",
                       f"microsegmentation state of {where}", where, "security")]
    return [_check("security", True, f"a rule lets {name(a_epg)} reach {name(b_epg)}, replies included", source, where)]


def _direction(facts: Facts, src: trace.End, dst: trace.End, result: dict, proto, port) -> list[dict]:
    """A one-way path's checks, in the traffic's order."""
    hops = result["hops"]
    checks = _port_checks(facts, src)
    routed = [h for h in hops if h["kind"] == "gateway"]
    where = routed[0]["switch"] if routed else (dst.switch or src.switch)
    checks += _rib_checks(facts, hops)
    checks += _microseg_checks(facts, src, dst, proto, port, where)
    stop = hops[-1]
    outcome = result["result"]
    if outcome == trace.DROP:
        checks.append(_check("path", False, stop["text"], stop["from"], stop["switch"], "segments"))
    elif outcome == trace.UNKNOWN:
        if dst.segment is not None and dst.segment["vni"] in facts.stretched:
            checks.append(_check("path", None, f"{dst.query} is not on this fabric's ports; segment {dst.segment['name']} "
                                 f"is stretched to {facts.stretched[dst.segment['vni']]}", "DC links"))
        else:
            checks.append(_check("path", None, stop["text"], stop["from"], stop["switch"]))
    checks += _port_checks(facts, dst)
    return checks


def reach(fabric: trace.Fabric, tables: trace.Tables, facts: Facts, source: str, destination: str,
          proto: int | None = None, port: int | None = None) -> dict:
    """The trace both ways and the first check that fails."""
    there = trace.trace(fabric, tables, source, destination)
    back = trace.trace(fabric, tables, destination, source)
    src, dst = trace.locate(fabric, tables, source, destination), trace.locate(fabric, tables, destination, source)
    ends_outside = there["result"] in (trace.FIREWALL, trace.ISP)
    forward = _direction(facts, src, dst, there, proto, port)
    # A path leaving through a service has no return path of ours to follow.
    reverse = [] if ends_outside else _direction(facts, dst, src, back, proto, port)
    for c in forward:
        c["direction"] = "there"
    for c in reverse:
        c["direction"] = "back"
    checks = forward + reverse
    stop = next((c for c in checks if c["ok"] is False), None)
    unsure = next((c for c in checks if c["ok"] is None), None)
    if stop is not None:
        word = "On the way back: " if stop["direction"] == "back" else ""
        sentence = f"{word}{'b' if word else 'B'}locked{' at ' + stop['switch'] if stop['switch'] else ''}: {stop['text']}"
        verdict = "blocked"
    elif there["result"] in (trace.FIREWALL, trace.ISP):
        sentence, verdict = there["hops"][-1]["text"], "leaves"
    elif unsure is not None:
        sentence, verdict = f"Not known: {unsure['text']}", "unknown"
    else:
        sentence, verdict = "Traffic can get there and back, as far as the switches show", "reaches"
    return {"verdict": verdict, "sentence": sentence, "stop": stop, "there": there, "back": back, "checks": checks,
            "switches": there["switches"]}


# -- reading (on demand, nothing stored) ------------------------------------------------------------

def protocol_number(proto: str | None) -> int | None:
    if not proto or proto == "any":
        return None
    if proto in PROTOCOLS:
        return PROTOCOLS[proto]
    if proto.isdigit() and int(proto) <= 255:
        return int(proto)
    raise ValueError(f"protocol {proto!r}: any, tcp, udp, icmp or a number 0-255")


async def _collected(prometheus, switches: list[str], facts: Facts) -> None:
    if prometheus is None:
        facts.unread.append("collector: no Prometheus")
        return
    wanted = "|".join(switches)
    for labels, _, value in await prometheus.query(f'fabric_interface_oper_up{{device=~"{wanted}"}}'):
        facts.ports_up.setdefault(labels["device"], {})[labels["interface"]] = value == 1
    for labels, _, _ in await prometheus.query(f'fabric_port_protection_tripped{{device=~"{wanted}"}} == 1'):
        facts.trips.setdefault((labels["device"], labels["interface"]), []).append(labels.get("cause", "acting"))


async def _on_path(reader, names: list[str], facts: Facts, mode: str) -> None:
    """Routing tables, and microsegmentation state while it is on, of the switches on the path."""
    bound = asyncio.Semaphore(PARALLEL)

    async def one(name: str) -> None:
        try:
            async with bound:
                facts.ribs[name] = (await reader.read(name, rib=True))[0] or []
                if mode != "off":
                    state = microseg.parse_state(await reader.get_state(name, microseg.STATE_FILTER))
                    facts.failed_rules[name] = {r["classifier"] for r in state["failed_rules"]}
        except Exception as exc:   # the answer goes on without it and says so
            facts.unread.append(f"{name}: {exc}")

    await asyncio.gather(*(one(n) for n in names))


async def run(nb, reader, prometheus, fabric: str, source: str, destination: str, proto: str | None, port: int | None) -> dict:
    addresses = [trace._ip(v) for v in (source, destination)]
    if all(addresses) and addresses[0].version != addresses[1].version:
        raise ValueError("one IPv4 and one IPv6 address: pick the same family for both ends")
    number = protocol_number(proto)
    if port is not None and (number not in (6, 17) or not 0 < port <= 65535):
        raise ValueError("a port needs tcp or udp, 1-65535")
    model, active = await trace.load(nb, fabric)
    trace.check_ends(model, source, destination)
    tables, unread = await trace.TableReader(reader).read(active)
    links = (await dci.document(nb)).get("links") or []
    facts = Facts(microseg=await microseg.document(nb, fabric), routed=await microseg._routed(nb, fabric), unread=unread)
    for link in links:
        others = [s["fabric"] for s in link["sides"] if s["fabric"] != fabric]
        if len(others) == 1:
            facts.stretched.update({s["vnis"][fabric]: others[0] for s in link.get("segments") or [] if fabric in s.get("vnis", {})})
    first = reach(model, tables, facts, source, destination, number, port)   # which switches are on the path
    names = [n for n in dict.fromkeys(
        [*first["there"]["switches"], *first["back"]["switches"],
         *(r["switch"] for way in ("there", "back") for h in first[way]["hops"] for r in h.get("routes", []))]) if n in active]
    await asyncio.gather(_on_path(reader, names, facts, facts.microseg.get("mode", "off")), _collected(prometheus, active, facts))
    return {"fabric": fabric, "architecture": model.architecture, **reach(model, tables, facts, source, destination, number, port),
            "read": sorted(tables.arp), "unread": facts.unread}


def known_hosts(fabric: trace.Fabric, tables: trace.Tables) -> list[dict]:
    """What the two boxes suggest: hosts from ARP, ND and MAC tables, services, gateways, and each
    switch's gateway in each segment it routes."""
    hosts: dict[str, dict] = {}
    for switch in fabric.switches:
        for s in trace.gateway_segments(fabric, switch):
            value = f"{switch}/{s['name']}"
            hosts[value] = {"value": value, "kind": "switch", "label": f"{switch}'s gateway in {s['name']}"}
    carried = {s.get("bd_id") for s in fabric.model.segments}   # not the management network's bridge domain
    for s in fabric.model.segments:
        for version in (4, 6):
            if gateway := trace._gateway(s, version):
                ip = str(ipaddress.ip_interface(gateway).ip)
                hosts[ip] = {"value": ip, "kind": "gateway", "label": f"gateway of {s['name']}"}
    for service in fabric.model.services:
        if service.get("next_hop"):
            hosts[service["next_hop"]] = {"value": service["next_hop"], "kind": "service", "label": service["name"]}
    for switch, entries in sorted(tables.arp.items()):
        for e in entries:
            if e.get("type") in ("dynamic-arp", "nd") and e.get("ip") and e["ip"] not in hosts and e.get("vpn") not in ("_management_vpn_", "_public_"):
                hosts[e["ip"]] = {"value": e["ip"], "kind": "host", "label": e.get("vpn") or switch}
    for switch, entries in sorted(tables.macs.items()):
        for e in entries:
            if not e.get("peer") and e.get("mac") and e["mac"] not in hosts and e.get("bd") in carried:
                hosts[e["mac"]] = {"value": e["mac"], "kind": "mac", "label": f"{switch} {e.get('interface')}"}
    return sorted(hosts.values(), key=lambda h: (h["kind"], h["value"]))


async def hosts(nb, reader, fabric: str) -> dict:
    model, active = await trace.load(nb, fabric)
    tables, unread = await trace.TableReader(reader).read(active)
    return {"hosts": known_hosts(model, tables), "unread": unread}
