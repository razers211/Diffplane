"""Microsegmentation: Huawei's EPGs and GBP contracts, never ACLs.

Intent lives in the fabric's config context (`fabric-<site>`, key `microsegmentation`), written
only here, read by the Microsegmentation generator:

    {"mode": "off" | "learn" | "enforce",
     "epgs": {"<id>": {"segment": <L2 VNI>}                                    # a routed segment's EPG
              "<id>": {"segment": <vni>, "name": "admins", "prefixes": [...], "macs": [...]},
              ... each may carry "inside": "deny" (default permit)},
     "contracts": [{"from": <id>, "to": <id>, "services": [{"protocol": "tcp", "ports": [5432, 5433]}],
                    "precedence": <1-511>}]}

Every routed segment is an EPG (its gateway subnets); `sync` gives a new routed segment its ID at
every segment publish, once the fabric's document exists (the first `set_mode` creates it). EPG IDs (1-65535) and contract precedences (1-511) are assigned once and
kept, so a change elsewhere never renumbers the switch's lines. An EPG of a segment that is no
longer routed stays in the document, inactive (the generator skips it and its contracts).
"""

import ipaddress
import re

from generators.microsegmentation import PROTOCOLS, contract_rules
from orchestrator import documents
from orchestrator.fabric_ntp import _context, _update_fabric
from orchestrator.segments import SEGMENT_NAME, SegmentError, context_name

KEY = "microsegmentation"
MODES = ("off", "learn", "enforce")
INSIDE = ("permit", "deny")
FIRST_ID, LAST_ID = 1, 65535          # V300R025: `traffic-segment segment-id <1-65535>`
LAST_PRECEDENCE = 511                 # huawei-microsegmentation: precedence 0..511, 512 pairs a policy
MAX_RULES = 409                       # rule numbers 10, 20, ... up to 4090 (priority 0..4095)
NAME_MAX = 31                         # segment-name 1..31
# Per chip (`display traffic-segment resource-usage`, a test switch on V300R025).
LIMITS = {"epgs": 8192, "members": 10240, "rules": 10237}
MAC = re.compile(r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}")


def empty() -> dict:
    return {"mode": "off", "epgs": {}, "contracts": []}


async def document(nb, fabric: str) -> dict:
    fabric_data = ((await _context(nb, fabric)).get("data") or {}).get("fabric") or {}
    return {**empty(), **(fabric_data.get(KEY) or {})}


async def _routed(nb, fabric: str) -> dict[int, dict]:
    """The fabric's routed segments as the segments context publishes them: {vni: entry}."""
    context = await documents.find(nb, context_name(fabric))
    segments = ((context or {}).get("data") or {}).get("segments") or {}
    return {int(vni): entry for vni, entry in segments.items() if entry.get("routing")}


async def _save(nb, fabric: str, doc: dict) -> None:
    await _update_fabric(nb, fabric, {KEY: doc}, drop=(KEY,))


# -- names and members ------------------------------------------------------------------------

def epg_name(epg: dict, segments: dict[int, dict]) -> str | None:
    """The EPG's name on the switch: its segment's name, or `<segment>-<group>`; None when its
    segment is not routed (inactive)."""
    segment = segments.get(int(epg["segment"]))
    if segment is None:
        return None
    return f"{segment['name']}-{epg['name']}" if epg.get("name") else segment["name"]


def segment_networks(segment: dict) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    routing = segment["routing"]
    nets = [ipaddress.IPv4Interface(routing["gateway"]).network]
    if (routing.get("ipv6") or {}).get("gateway"):
        nets.append(ipaddress.IPv6Interface(routing["ipv6"]["gateway"]).network)
    return nets


def _free(used: set[int], first: int, last: int, what: str) -> int:
    for n in range(first, last + 1):
        if n not in used:
            return n
    raise SegmentError(f"no free {what} left ({first}-{last})")


def _prefixes(values: list[str], segment: dict) -> list[str]:
    nets, inside = [], segment_networks(segment)
    for value in values:
        try:
            net = ipaddress.ip_network(value.strip(), strict=True)
        except ValueError as exc:
            raise SegmentError(f"prefix {value!r}: {exc}") from exc
        if not any(net.version == s.version and net.subnet_of(s) for s in inside):
            raise SegmentError(f"prefix {net} is not inside segment {segment['name']}'s subnets "
                               f"({', '.join(str(s) for s in inside)})")
        if net in inside:   # V300: a prefix belongs to one EPG only (a test switch)
            raise SegmentError(f"prefix {net} is segment {segment['name']}'s whole subnet, already its own EPG: "
                               "a group takes a smaller part of it")
        if str(net) in nets:
            raise SegmentError(f"prefix {net} is listed twice")
        nets.append(str(net))
    return nets


def _macs(values: list[str]) -> list[str]:
    macs = []
    for value in values:
        mac = value.strip().lower().replace(":", "").replace("-", "").replace(".", "")
        mac = "-".join(mac[i:i + 4] for i in range(0, len(mac), 4))
        if not MAC.fullmatch(mac):
            raise SegmentError(f"MAC {value!r}: 12 hex digits, e.g. 0200-fc00-0001")
        if mac in macs:
            raise SegmentError(f"MAC {mac} is listed twice")
        macs.append(mac)
    return macs


def _services(values: list[dict]) -> list[dict]:
    if not values:
        raise SegmentError("a contract needs at least one service (protocol any allows everything)")
    result = []
    for value in values:
        protocol = value.get("protocol", "any")
        if isinstance(protocol, str):
            protocol = protocol.strip().lower()
            if protocol not in PROTOCOLS:
                raise SegmentError(f"protocol {protocol!r}: any, tcp, udp, icmp or a number 0-255")
        elif not (isinstance(protocol, int) and 0 <= protocol <= 255):
            raise SegmentError(f"protocol {protocol!r}: any, tcp, udp, icmp or a number 0-255")
        ports = value.get("ports")
        if ports:
            if protocol not in ("tcp", "udp"):
                raise SegmentError("ports are only for tcp or udp")
            ports = [int(p) for p in ports]
            if len(ports) == 1:
                ports = [ports[0], ports[0]]
            if len(ports) != 2 or not 0 <= ports[0] <= ports[1] <= 65535 or ports == [0, 65535]:
                raise SegmentError(f"ports {value.get('ports')!r}: one port or a range a-b (not 0-65535: leave ports out)")
        service = {"protocol": protocol, **({"ports": ports} if ports else {})}
        if service in result:
            raise SegmentError(f"service {service} is listed twice")
        result.append(service)
    return result


# -- placement estimate (members per VPN instance, per chip) ------------------------------------

def member_vpns(vni: int, segments: dict[int, dict]) -> int:
    """VPN instances a segment's prefixes are written with: its own, peered segments' (either
    side lists the peering) and its firewall service's (Huawei looks the destination EPG up with
    the source VPN instance between instances)."""
    routing = segments[vni]["routing"]
    peers = set(routing.get("peers") or ()) | {v for v, s in segments.items() if vni in (s["routing"].get("peers") or ())}
    return 1 + len(peers & set(segments)) + (1 if routing.get("firewall") else 0)


def estimate(doc: dict, segments: dict[int, dict]) -> dict:
    """The most a switch holds if it has every instance (a border leaf, a centralized gateway)."""
    epgs = members = 0
    for epg in doc["epgs"].values():
        vni = int(epg["segment"])
        if vni not in segments:
            continue
        epgs += 1
        prefixes = len(epg.get("prefixes") or ()) if epg.get("name") else len(segment_networks(segments[vni]))
        members += prefixes * member_vpns(vni, segments) + len(epg.get("macs") or ())
    rules = sum(len(rules_of(c, doc, segments)) for c in doc["contracts"])
    return {"epgs": epgs, "members": members, "rules": rules, "limits": LIMITS}


def _check_limits(doc: dict, segments: dict[int, dict]) -> None:
    usage = estimate(doc, segments)
    over = [f"{k} {usage[k]} > {LIMITS[k]}" for k in LIMITS if usage[k] > LIMITS[k]]
    if over:
        raise SegmentError("over the switch's microsegmentation resources: " + ", ".join(over))


def families(epg: dict, segments: dict[int, dict]) -> set[int]:
    """IP versions an EPG's traffic has: its segment's (a group inherits them)."""
    return {n.version for n in segment_networks(segments[int(epg["segment"])])}


def rules_of(contract: dict, doc: dict, segments: dict[int, dict]) -> list:
    """The contract's rules as the generator writes them (none while an EPG is inactive)."""
    a, b = doc["epgs"].get(str(contract["from"])), doc["epgs"].get(str(contract["to"]))
    if a is None or b is None or int(a["segment"]) not in segments or int(b["segment"]) not in segments:
        return []
    return contract_rules(contract["from"], contract["to"], families(a, segments) & families(b, segments),
                          contract["services"])


# -- reads and writes ---------------------------------------------------------------------------

async def get(nb, fabric: str) -> dict:
    doc, segments = await document(nb, fabric), await _routed(nb, fabric)
    epgs = []
    for key, epg in sorted(doc["epgs"].items(), key=lambda i: int(i[0])):
        vni = int(epg["segment"])
        epgs.append({"id": int(key), "segment": vni, "group": epg.get("name"), "name": epg_name(epg, segments),
                     "active": vni in segments, "inside": epg.get("inside") or "permit",
                     "prefixes": (list(epg.get("prefixes") or ()) if epg.get("name")
                                  else [str(n) for n in segment_networks(segments[vni])] if vni in segments else []),
                     "macs": list(epg.get("macs") or ())})
    return {"mode": doc["mode"], "epgs": epgs, "contracts": doc["contracts"], "usage": estimate(doc, segments)}


async def sync(nb, fabric: str, segments: dict) -> None:
    """Give every routed segment without an EPG its ID (called by each segment publish, with the
    `segments` it publishes)."""
    context = await documents.find(nb, f"fabric-{fabric}")
    stored = (((context or {}).get("data") or {}).get("fabric") or {}).get(KEY)
    if stored is None:   # never set up in this fabric: nothing to number yet (set_mode does it)
        return
    doc = {**empty(), **stored}
    have = {int(e["segment"]) for e in doc["epgs"].values() if not e.get("name")}
    routed = sorted(int(v) for v, s in segments.items() if s.get("routing"))
    missing = [v for v in routed if v not in have]
    if not missing:
        return
    epgs = dict(doc["epgs"])
    for vni in missing:
        epgs[str(_free({int(k) for k in epgs}, FIRST_ID, LAST_ID, "EPG ID"))] = {"segment": vni}
    await _save(nb, fabric, {**doc, "epgs": epgs})


async def set_mode(nb, fabric: str, mode: str) -> dict:
    if mode not in MODES:
        raise SegmentError(f"mode {mode!r}: off, learn or enforce")
    doc = await document(nb, fabric)
    await _save(nb, fabric, {**doc, "mode": mode})
    context = await documents.find(nb, context_name(fabric))
    await sync(nb, fabric, ((context or {}).get("data") or {}).get("segments") or {})
    return {"mode": mode}


def _epg(doc: dict, epg_id: int) -> dict:
    epg = doc["epgs"].get(str(epg_id))
    if epg is None:
        raise SegmentError(f"no EPG {epg_id}")
    return epg


async def set_group(nb, fabric: str, segment: int, name: str, prefixes: list[str], macs: list[str],
                    inside: str | None = None, epg_id: int | None = None) -> dict:
    """Create (no `epg_id`) or replace a named EPG inside a routed segment."""
    doc, segments = await document(nb, fabric), await _routed(nb, fabric)
    if segment not in segments:
        raise SegmentError(f"segment {segment} is not routed: only a routed segment's hosts can form a group")
    name = name.strip()
    if not SEGMENT_NAME.fullmatch(name):
        raise SegmentError(f"group name {name!r}: letters, digits, - and _, starting with a letter")
    full = f"{segments[segment]['name']}-{name}"
    if len(full) > NAME_MAX:
        raise SegmentError(f"EPG name {full!r} is longer than {NAME_MAX} characters: shorten the group name")
    if inside not in (None, *INSIDE):
        raise SegmentError(f"inside {inside!r}: permit or deny")
    entry = {"segment": segment, "name": name, "prefixes": _prefixes(prefixes, segments[segment]), "macs": _macs(macs)}
    if not entry["prefixes"] and not entry["macs"]:
        raise SegmentError("a group needs at least one prefix or MAC")
    if inside == "deny":
        entry["inside"] = "deny"
    epgs = dict(doc["epgs"])
    if epg_id is not None:
        current = _epg(doc, epg_id)
        if not current.get("name"):
            raise SegmentError(f"EPG {epg_id} is segment {current['segment']}'s own: only its inside behaviour can change")
        if int(current["segment"]) != segment:
            raise SegmentError("a group cannot move to another segment: delete it and create it there")
    for key, other in epgs.items():
        if key != str(epg_id) and epg_name(other, segments) == full:
            raise SegmentError(f"an EPG named {full} exists (EPG {key})")
        if key != str(epg_id) and other.get("name") and int(other["segment"]) == segment:
            taken = sorted(set(entry["prefixes"]) & set(other.get("prefixes") or ()))
            taken += sorted(set(entry["macs"]) & set(other.get("macs") or ()))
            if taken:   # V300: a member belongs to one EPG only
                raise SegmentError(f"{', '.join(taken)} already in EPG {key} ({epg_name(other, segments)})")
    if any(s["name"] == full for s in segments.values()):
        raise SegmentError(f"{full} is a segment's name: choose another group name")
    key = str(epg_id) if epg_id is not None else str(_free({int(k) for k in epgs}, FIRST_ID, LAST_ID, "EPG ID"))
    epgs[key] = entry
    new = {**doc, "epgs": epgs}
    _check_limits(new, segments)
    await _save(nb, fabric, new)
    return {"id": int(key), **entry}


async def set_inside(nb, fabric: str, epg_id: int, inside: str) -> dict:
    """Traffic between hosts of one EPG: permit (the default) or deny; also a segment's own EPG."""
    if inside not in INSIDE:
        raise SegmentError(f"inside {inside!r}: permit or deny")
    doc = await document(nb, fabric)
    epg = {k: v for k, v in _epg(doc, epg_id).items() if k != "inside"}
    if inside == "deny":
        epg["inside"] = "deny"
    await _save(nb, fabric, {**doc, "epgs": {**doc["epgs"], str(epg_id): epg}})
    return {"id": epg_id, "inside": inside}


async def delete_group(nb, fabric: str, epg_id: int) -> None:
    doc = await document(nb, fabric)
    epg = _epg(doc, epg_id)
    if not epg.get("name"):
        raise SegmentError(f"EPG {epg_id} is a segment's own: it goes away only with the segment's routing")
    users = [c for c in doc["contracts"] if epg_id in (c["from"], c["to"])]
    if users:
        raise SegmentError(f"EPG {epg_id} is used by {len(users)} contract(s): delete them first")
    await _save(nb, fabric, {**doc, "epgs": {k: v for k, v in doc["epgs"].items() if k != str(epg_id)}})


async def set_contract(nb, fabric: str, source: int, destination: int, services: list[dict]) -> dict:
    """Allow `source` -> `destination` for these services (replaces an existing contract between
    them, keeping its precedence); the reply rules are added by the generator."""
    doc, segments = await document(nb, fabric), await _routed(nb, fabric)
    _epg(doc, source), _epg(doc, destination)
    if source == destination:
        raise SegmentError("traffic inside one EPG is its inside behaviour (permit or deny), not a contract")
    contract = {"from": source, "to": destination, "services": _services(services)}
    contracts = [c for c in doc["contracts"] if (c["from"], c["to"]) != (source, destination)]
    current = next((c for c in doc["contracts"] if (c["from"], c["to"]) == (source, destination)), None)
    contract["precedence"] = (current["precedence"] if current else
                              _free({c["precedence"] for c in contracts}, 1, LAST_PRECEDENCE, "contract slot"))
    new = {**doc, "contracts": sorted(contracts + [contract], key=lambda c: c["precedence"])}
    if len(rules_of(contract, new, segments)) > MAX_RULES:
        raise SegmentError(f"a contract holds at most {MAX_RULES} rules (each service twice per IP version)")
    _check_limits(new, segments)
    await _save(nb, fabric, new)
    return contract


async def delete_contract(nb, fabric: str, source: int, destination: int) -> None:
    doc = await document(nb, fabric)
    contracts = [c for c in doc["contracts"] if (c["from"], c["to"]) != (source, destination)]
    if len(contracts) == len(doc["contracts"]):
        raise SegmentError(f"no contract from EPG {source} to EPG {destination}")
    await _save(nb, fabric, {**doc, "contracts": contracts})


async def restore(nb, fabric: str, doc: dict) -> None:
    """Rollback: the document as at the rollback point, then IDs for segments routed since."""
    await _save(nb, fabric, {**empty(), **doc})
    context = await documents.find(nb, context_name(fabric))
    await sync(nb, fabric, ((context or {}).get("data") or {}).get("segments") or {})


# -- switches that must run it ----------------------------------------------------------------

MODULE = "huawei-microsegmentation"   # announced from V300R025 (a test switch: R023 had none)


async def switches(nb, fabric: str) -> list[str]:
    """Active switches the generator writes it on (generators.fabric.has_overlay): every leaf, and in
    a centralized fabric also the gateway pair and any M-LAG spine pair."""
    data = ((await _context(nb, fabric)).get("data") or {}).get("fabric") or {}
    devices = await nb.find("dcim/devices", site=fabric, status="active")
    leaves = {d["name"] for d in devices if (d.get("role") or {}).get("slug") in ("leaf", "border-leaf")}
    if (data.get("architecture") or "symmetric") == "centralized":
        # the gateway pair and the layer-2 leaves, and an extra M-LAG spine pair
        overlay = set(data.get("gateway_pair") or ()) | {d["name"] for d in devices if (d.get("custom_fields") or {}).get("mlag_peer")}
        return sorted(d["name"] for d in devices if d["name"] in overlay | leaves)
    return sorted(leaves)


async def check_software(nb, fabric: str, has_module) -> None:
    """Refuse turning it on while an active switch that would get it does not announce the
    module (`has_module(name)` -> bool, reading the switch's NETCONF capabilities)."""
    missing = []
    for name in await switches(nb, fabric):
        try:
            if not await has_module(name):
                missing.append(f"{name} (no {MODULE}: V300R025 or later is needed)")
        except SegmentError as exc:
            missing.append(f"{name} ({exc})")
    if missing:
        raise SegmentError("microsegmentation cannot be turned on: " + "; ".join(missing))


# -- state read from a switch (a test switch probe) ----------------------------------

NS = "urn:huawei:yang:huawei-microsegmentation"
STATE_FILTER = (f'<microsegmentation xmlns="{NS}"><resources/><classifier-based-statistics/>'
                '<traffic-segment-policy-states/><ipv4-member-states/></microsegmentation>')
COUNTERS = ("mached-packets", "mached-bytes", "passed-packets", "passed-bytes", "dropped-packets", "dropped-bytes")


def parse_state(data_xml: str) -> dict:
    """TCAM use per chip, each classifier's (contract's) counters summed over slots, and the rules
    and members the switch could not install (state other than success)."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(data_xml)
    tag = lambda name: f"{{{NS}}}{name}"
    text = lambda el, name: el.findtext(tag(name)) or ""
    number = lambda el, name: int(text(el, name) or 0)
    chips = [{"slot": text(r, "slot-id"), "chip": text(r, "chip-id"),
              **{k.replace("-", "_"): number(r, k) for k in (
                  "tcam-block-total", "tcam-block-used", "tcam-block-limit", "epg-used", "epg-free",
                  "epg-member-used", "epg-member-free", "gbp-rule-used", "gbp-rule-free")}}
             for r in root.iter(tag("resource"))]
    contracts: dict[str, dict] = {}
    for c in root.iter(tag("classifier-based-statistic")):
        counters = contracts.setdefault(text(c, "classifier-name"), {k.replace("mached", "matched").replace("-", "_"): 0 for k in COUNTERS})
        for k in COUNTERS:
            counters[k.replace("mached", "matched").replace("-", "_")] += number(c, k)
    failed_rules = [{"classifier": text(r, "classifier-name"), "rule": number(r, "rule-priority"), "state": text(r, "state")}
                    for r in root.iter(tag("traffic-segment-policy-state")) if text(r, "state") != "success"]
    failed_members = [{"epg": number(m, "segment-id"), "member": f"{text(m, 'ip-address')}/{text(m, 'ip-mask-length')}",
                       "vpn": text(m, "vpn-name"), "state": text(m, "state")}
                      for m in root.iter(tag("ipv4-member-state")) if text(m, "state") != "success"]
    return {"chips": chips, "contracts": contracts, "failed_rules": failed_rules, "failed_members": failed_members}


async def state(reader, name: str) -> dict:
    """Read now from one switch (nothing stored), with the collector's read account."""
    return {"device": name, **parse_state(await reader.get_state(name, STATE_FILTER))}
