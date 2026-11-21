"""DC links: EVPN-VXLAN DCI with re-origination at each fabric's border pair.

One platform document, the config context `dci` (key `dci`), assigned to every linked fabric and
written only here, read by the Dci and BridgeDomains generators:

    {"links": [{"name": "east-west", "split_group": "DCI",
                "sides": [{"fabric": "example", "pair": ["leaf1", "leaf2"],
                           "uplinks": {"leaf1": [{"port": "100GE1/0/3", "address": "10.255.10.1/30",
                                                  "neighbour": "10.255.10.2", "asn": 65004}]},
                           # derived from NetBox at every save, for the other side's generator:
                           "asn": 65001, "loopbacks": {"leaf1": "10.0.0.3", ...}, "vtep": "10.0.1.1"},
                          {...the other fabric...}],
                "segments": [{"name": "web", "vnis": {"example": 10500, "example-pip": 10500}, "dci_vni": 60000}]}],
     "retired": {"<switch>": {"peers": [...], "ports": [...]}},
     "ready": ["<switch>", ...]}

A switch is ready once a verified deploy put its DC links' BGP peers on it. VRP refuses a bridge
domain's `vxlan vni <n> split-group <group>` until a BGP peer in that split group exists,
and Annet sends bridge domains before `bgp` in every patch, so a segment is stretched only
over a link whose active border switches are all ready.

Each side is an M-LAG pair of its fabric (chosen per DC link). An uplink carries eBGP to the
other DC's border (dark fibre) or to a provider PE (L3 VPN) and only the borders' loopbacks and VTEP.
Linked fabrics must share the gateway MAC. What a link no longer has stays retired, so the
generator keeps owning it, until a verified deploy of that switch has removed it (as services are).
"""

import ipaddress
import re

from orchestrator import documents
from orchestrator.fabric_ntp import _context
from orchestrator.segments import L3_VNI_OFFSET, SegmentError, Segments, _cf, _ref_id, context_name

CONTEXT = "dci"
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,30}")
LAST_VNI = 16777215
FIRST_DCI_VNI = 60000   # from 60000 up, so it fits a 4-byte-ASN fabric's 1-65535 too


def empty() -> dict:
    return {"links": [], "retired": {}, "ready": []}


async def document(nb) -> dict:
    context = await documents.find(nb, CONTEXT)
    return {**empty(), **(((context or {}).get("data") or {}).get(CONTEXT) or {})}


async def _save(nb, doc: dict) -> None:
    fabrics = sorted({side["fabric"] for link in doc["links"] for side in link["sides"]})
    sites = [s["id"] for s in await nb.find("dcim/sites") if s["slug"] in fabrics]
    # Retired entries must keep reaching their switches' fabric even after its last link went.
    retired_sites = {d["site"]["id"] for d in await nb.find("dcim/devices") if d["name"] in doc["retired"] and d.get("site")}
    sites = sorted(set(sites) | retired_sites)
    data = {CONTEXT: doc}
    await documents.put(nb, CONTEXT, data, sites)


def owned(doc: dict) -> dict[str, dict[str, set[str]]]:
    """Per switch, the BGP peers and uplink ports the links give it (what the generator owns)."""
    found: dict[str, dict[str, set[str]]] = {}
    for link in doc["links"]:
        for local, remote in ((link["sides"][0], link["sides"][1]), (link["sides"][1], link["sides"][0])):
            for switch in local["pair"]:
                own = found.setdefault(switch, {"peers": set(), "ports": set()})
                own["peers"] |= set(remote["loopbacks"].values())
                for uplink in local["uplinks"].get(switch, ()):
                    own["peers"].add(uplink["neighbour"])
                    own["ports"].add(uplink["port"])
    return found


def peers_of(doc: dict, switch: str) -> set[str]:
    """Every BGP peer address of a switch's DC links, current or retired (routing import skips them)."""
    current = owned(doc).get(switch, {}).get("peers", set())
    return current | set((doc["retired"].get(switch) or {}).get("peers") or ())


def _retire(old: dict, new: dict) -> dict:
    before, after = owned(old), owned(new)
    retired = {s: {k: set(v) for k, v in e.items()} for s, e in old["retired"].items()}
    for switch, own in before.items():
        for kind in ("peers", "ports"):
            gone = own[kind] - after.get(switch, {}).get(kind, set())
            if gone:
                retired.setdefault(switch, {"peers": set(), "ports": set()}).setdefault(kind, set()).update(gone)
    # Something owned again is no longer retired.
    for switch, own in after.items():
        for kind in ("peers", "ports"):
            if switch in retired:
                retired[switch][kind] = set(retired[switch].get(kind, ())) - own[kind]
    return {s: {k: sorted(v) for k, v in e.items() if v} for s, e in retired.items() if any(e.values())}


# -- sides ----------------------------------------------------------------------------------------

async def _fabric(nb, fabric: str) -> dict:
    data = ((await _context(nb, fabric)).get("data") or {}).get("fabric") or {}
    if not data:
        raise SegmentError(f"no fabric {fabric!r}")
    return data


async def _loopback(nb, device_id: int, interface: str) -> str | None:
    iface = await nb.find_one("dcim/interfaces", device_id=device_id, name=interface)
    if iface is None:
        return None
    ips = await nb.find("ipam/ip-addresses", interface_id=iface["id"])
    return ips[0]["address"].split("/")[0] if ips else None


async def _side(nb, side: dict) -> dict:
    fabric = side.get("fabric")
    settings = await _fabric(nb, fabric)
    pair = list(side.get("pair") or ())
    if len(pair) != 2 or pair[0] == pair[1]:
        raise SegmentError(f"{fabric}: a border is an M-LAG pair: two switches")
    devices = {}
    for name in pair:
        device = await nb.find_one("dcim/devices", name=name)
        if device is None or (device.get("site") or {}).get("slug") != fabric:
            raise SegmentError(f"no switch {name!r} in fabric {fabric}")
        devices[name] = device
    peer = _cf(devices[pair[0]], "mlag_peer")
    if _ref_id(peer) != devices[pair[1]]["id"]:
        raise SegmentError(f"{pair[0]} and {pair[1]} are not an M-LAG pair")
    loopbacks = {}
    for name, device in devices.items():
        address = await _loopback(nb, device["id"], "LoopBack0")
        if address is None:
            raise SegmentError(f"{name} has no LoopBack0 address yet: seed its fabric first")
        loopbacks[name] = address
    on = "LoopBack2" if settings.get("pip_prefix") else "LoopBack1"   # the PIP layout (generators/fabric.py)
    vtep = await _loopback(nb, devices[pair[0]]["id"], on)
    if vtep is None:
        raise SegmentError(f"{pair[0]} has no shared VTEP ({on}) yet")
    site_id = _ref_id(devices[pair[0]].get("site"))
    services = await Segments(nb)._service_ports(site_id)
    uplinks = {}
    for switch, items in (side.get("uplinks") or {}).items():
        if switch not in devices:
            raise SegmentError(f"uplink on {switch}: only the border pair {pair[0]}/{pair[1]} has DCI uplinks")
        uplinks[switch] = [await _uplink(nb, devices[switch], item, services) for item in items]
        ports = [u["port"] for u in uplinks[switch]]
        if len(set(ports)) != len(ports):
            raise SegmentError(f"{switch}: a port is listed twice")
    return {"fabric": fabric, "pair": pair, "uplinks": {s: u for s, u in uplinks.items() if u},
            "asn": int(settings["asn"]), "loopbacks": loopbacks, "vtep": vtep}


async def _uplink(nb, device: dict, item: dict, services) -> dict:
    name, port = device["name"], item.get("port")
    iface = await nb.find_one("dcim/interfaces", device_id=device["id"], name=port)
    if iface is None:
        raise SegmentError(f"{name} has no interface {port!r}")
    kind = (iface.get("type") or {}).get("value")
    if kind in ("lag", "virtual") or port.startswith("MEth") or "." in port or iface.get("lag"):
        raise SegmentError(f"{name} {port}: a DCI uplink is a free physical port (not a trunk, member, sub-interface or MEth)")
    if await nb.find("ipam/ip-addresses", interface_id=iface["id"]):
        raise SegmentError(f"{name} {port} already has an address (a fabric link?)")
    if _cf(iface, "segments"):
        raise SegmentError(f"{name} {port} carries segments")
    if (device["id"], port) in services:
        raise SegmentError(f"{name} {port} is a service's port")
    try:
        address = ipaddress.IPv4Interface(item.get("address"))
        neighbour = ipaddress.IPv4Address(item.get("neighbour"))
    except (ValueError, TypeError) as exc:
        raise SegmentError(f"{name} {port}: {exc}") from exc
    if address.network.prefixlen not in (30, 31):
        raise SegmentError(f"{name} {port}: a DCI uplink is a /30 or /31")
    if neighbour not in address.network or neighbour == address.ip:
        raise SegmentError(f"{name} {port}: neighbour {neighbour} is not the other end of {address.network}")
    asn = item.get("asn")
    if not isinstance(asn, int) or not 1 <= asn <= 4294967295:
        raise SegmentError(f"{name} {port}: the neighbour's ASN is 1-4294967295")
    return {"port": port, "address": str(address), "neighbour": str(neighbour), "asn": asn}


# -- reads and writes -----------------------------------------------------------------------------

def _link(doc: dict, name: str) -> dict:
    link = next((l for l in doc["links"] if l["name"] == name), None)
    if link is None:
        raise SegmentError(f"no DC link {name!r}")
    return link


async def get(nb) -> dict:
    return await document(nb)


async def set_link(nb, name: str, sides: list[dict], split_group: str = "DCI") -> dict:
    """Create or replace a DC link's two sides (its stretched segments are kept)."""
    if not NAME.fullmatch(name or ""):
        raise SegmentError(f"link name {name!r}: letters, digits, - and _, starting with a letter, at most 31")
    if not NAME.fullmatch(split_group or ""):
        raise SegmentError(f"split group {split_group!r}: letters, digits, - and _, at most 31")
    if len(sides) != 2 or sides[0].get("fabric") == sides[1].get("fabric"):
        raise SegmentError("a DC link joins two different fabrics")
    built = [await _side(nb, s) for s in sides]
    a, b = (await _fabric(nb, built[0]["fabric"])), (await _fabric(nb, built[1]["fabric"]))
    if (a.get("gateway_mac") or "").lower() != (b.get("gateway_mac") or "").lower():
        raise SegmentError(f"{built[0]['fabric']} and {built[1]['fabric']} have different gateway MACs "
                           f"({a.get('gateway_mac')} and {b.get('gateway_mac')}): linked fabrics must share it (Fabric page)")
    doc = await document(nb)
    others = [l for l in doc["links"] if l["name"] != name]
    for other in others:
        taken = {s for side in other["sides"] for s in side["pair"]} & {s for side in built for s in side["pair"]}
        if taken:
            raise SegmentError(f"{', '.join(sorted(taken))} already border(s) of DC link {other['name']}")
    current = next((l for l in doc["links"] if l["name"] == name), None)
    segments = (current or {}).get("segments") or []
    if current is not None and {s["fabric"] for s in current["sides"]} != {s["fabric"] for s in built}:
        if segments:
            raise SegmentError(f"DC link {name} stretches segments: remove them before changing its fabrics")
    link = {"name": name, "split_group": split_group, "sides": built, "segments": segments}
    if current is not None and segments and current["split_group"] != split_group:
        raise SegmentError(f"DC link {name} stretches segments: its split group cannot change meanwhile")
    new = {"links": sorted(others + [link], key=lambda l: l["name"]), "retired": {},
           # a border that is new to the link (or the link itself) is not ready until verified
           "ready": sorted(set(doc["ready"]) & {s for side in (current or {"sides": []})["sides"] for s in side["pair"]}
                           & {s for side in built for s in side["pair"]})}
    new["retired"] = _retire(doc, new)
    await _save(nb, new)
    return link


async def delete_link(nb, name: str) -> None:
    doc = await document(nb)
    _link(doc, name)
    gone = {s for side in _link(doc, name)["sides"] for s in side["pair"]}
    new = {"links": [l for l in doc["links"] if l["name"] != name], "retired": {}, "ready": sorted(set(doc["ready"]) - gone)}
    new["retired"] = _retire(doc, new)
    await _save(nb, new)


async def _segments(nb, fabric: str) -> dict[int, dict]:
    context = await documents.find(nb, context_name(fabric))
    return {int(v): s for v, s in (((context or {}).get("data") or {}).get("segments") or {}).items()}


async def set_segment(nb, link_name: str, name: str, vnis: dict[str, int]) -> dict:
    """Stretch a segment over a DC link: the segment `name` of each fabric (each its own VNI),
    carried between the borders with a DCI VNI the platform picks once and keeps: the lowest free from 60000, which neither fabric uses as a segment or L3 VNI."""
    doc = await document(nb)
    link = _link(doc, link_name)
    fabrics = [s["fabric"] for s in link["sides"]]
    if set(vnis) != set(fabrics):
        raise SegmentError(f"give the segment's VNI in each of {' and '.join(fabrics)}")
    gateways, used = {}, set()
    for fabric in fabrics:
        known = await _segments(nb, fabric)
        vni = int(vnis[fabric])
        if vni not in known:
            raise SegmentError(f"no segment {vni} in fabric {fabric}")
        if known[vni]["name"] != name:
            raise SegmentError(f"segment {vni} of {fabric} is {known[vni]['name']!r}: a stretched segment has the same "
                               f"name on both sides ({name!r})")
        gateways[fabric] = _gateway(known[vni])
        used |= set(known) | {v + L3_VNI_OFFSET for v in known}
    a, b = (gateways[f] for f in fabrics)
    if a is not None and b is not None and a != b:
        # shortcut: IPv6 gateways of a stretched segment are not aligned; add it when one is stretched with IPv6.
        raise SegmentError(f"{name} is routed with {a} in {fabrics[0]} and {b} in {fabrics[1]}: a stretched segment has "
                           "one gateway subnet (); make one side not routed, it takes the other's when routed again")
    for other in doc["links"]:
        for s in other["segments"]:
            if not (other["name"] == link_name and s["name"] == name):
                used.add(int(s["dci_vni"]))
            if other["name"] != link_name and any(int(s["vnis"].get(f, -1)) == int(vnis[f]) for f in fabrics):
                raise SegmentError(f"{name} is already stretched over DC link {other['name']}")
    borders = [s for side in link["sides"] for s in side["pair"]]
    active = {d["name"] for d in await nb.find("dcim/devices", status="active")
              if d["name"] in borders and ((d.get("status") or {}).get("value") or d.get("status")) == "active"}
    waiting = sorted(active - set(doc["ready"]))
    if waiting and not any(s["name"] == name for s in link["segments"]):
        raise SegmentError(f"deploy DC link {link_name} first: {', '.join(waiting)} not verified with it yet "
                           "(a bridge domain's split group needs the link's BGP peers on the switch)")
    current = next((s for s in link["segments"] if s["name"] == name), None)
    if current is not None and current["vnis"] == {f: int(vnis[f]) for f in fabrics}:
        dci_vni = int(current["dci_vni"])   # kept: never renumbered
    else:
        four_byte = any(int(side["asn"]) > 65535 for side in link["sides"])
        dci_vni = _free_vni(used, 65535 if four_byte else LAST_VNI)
    entry = {"name": name, "vnis": {f: int(vnis[f]) for f in fabrics}, "dci_vni": dci_vni}
    link["segments"] = sorted([s for s in link["segments"] if s["name"] != name] + [entry], key=lambda s: s["name"])
    await _save(nb, doc)
    return entry


def _free_vni(used: set[int], last: int) -> int:
    for vni in range(FIRST_DCI_VNI, last + 1):
        if vni not in used:
            return vni
    raise SegmentError(f"no free DCI VNI left ({FIRST_DCI_VNI}-{last})")


def _gateway(segment: dict | None):
    routing = (segment or {}).get("routing") or {}
    return ipaddress.ip_interface(routing["gateway"]).network if routing.get("gateway") else None


async def peer_gateway(nb, fabric: str, vni: int):
    """(other fabric, its gateway subnet) when the fabric's segment is stretched and routed there."""
    for link in (await document(nb))["links"]:
        for s in link["segments"]:
            if int(s["vnis"].get(fabric, -1)) != vni:
                continue
            other = next(f for f in s["vnis"] if f != fabric)
            gateway = _gateway((await _segments(nb, other)).get(int(s["vnis"][other])))
            return (other, gateway) if gateway is not None else None
    return None


async def delete_segment(nb, link_name: str, name: str) -> None:
    doc = await document(nb)
    link = _link(doc, link_name)
    if not any(s["name"] == name for s in link["segments"]):
        raise SegmentError(f"{name} is not stretched over {link_name}")
    link["segments"] = [s for s in link["segments"] if s["name"] != name]
    await _save(nb, doc)


async def stretched(nb, fabric: str, vni: int) -> str | None:
    """The DC link stretching a fabric's segment, if any (segment deletion is refused meanwhile)."""
    for link in (await document(nb))["links"]:
        if any(int(s["vnis"].get(fabric, -1)) == vni for s in link["segments"]):
            return link["name"]
    return None


async def restore(nb, fabric: str, links: list[dict]) -> None:
    """Rollback: the fabric's DC links as at the rollback point. A link whose borders are not
    the same now comes back without its stretched segments: they need the link deployed first."""
    doc = await document(nb)
    current = {l["name"]: l for l in doc["links"]}
    kept = [l for l in doc["links"] if not any(s["fabric"] == fabric for s in l["sides"])]
    back, still = [], {s for l in kept for side in l["sides"] for s in side["pair"]}
    for link in links:
        now = current.get(link["name"])
        same = now is not None and [s["pair"] for s in now["sides"]] == [s["pair"] for s in link["sides"]]
        back.append({**link, "segments": link["segments"] if same else []})
        if same:
            still |= {s for side in link["sides"] for s in side["pair"]}
    new = {"links": sorted(kept + back, key=lambda l: l["name"]), "retired": {}, "ready": sorted(set(doc["ready"]) & still)}
    new["retired"] = _retire(doc, new)
    await _save(nb, new)


async def verified(nb, switch: str) -> None:
    """After a verified deploy of `switch`: what its DC links no longer have is gone from it, and
    the links it is a border of are on it (ready to stretch segments over)."""
    doc = await document(nb)
    border = any(switch in side["pair"] for link in doc["links"] for side in link["sides"])
    ready = sorted(set(doc["ready"]) | {switch}) if border else doc["ready"]
    if switch in doc["retired"] or ready != doc["ready"]:
        doc["retired"] = {s: e for s, e in doc["retired"].items() if s != switch}
        doc["ready"] = ready
        await _save(nb, doc)
