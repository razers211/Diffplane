"""Health scores: each switch, link between fabric switches and segment starts
at 100 and loses a fixed amount per problem found, never below 0; the reasons are listed. A fabric's
score is its worst switch's or link's.

Deductions (per occurrence):
  -40  switch not polled, power module failed, M-LAG peer-link or heartbeat down
  -20  BGP/EVPN or OSPF peer down, a link in use down, an optic alarm, a fan missing, a temperature at Major
       (a down Eth-Trunk once, its down members named in that reason),
       a flapping link (3 downs in 30 minutes, once with a down, the reason says so)
  -10  out of sync, an optic warning, a temperature at Minor, CPU or memory over the switch's
       threshold, errors rising on a port in use, a port in use that edge-port protection acts on
       (storm, loop or BPDU)

Planned switches are not scored, and a planned partner never counts: a port cabled to a planned
switch, the peer-link of a pair whose peer is planned, and peers the control plane marks
expected down. Everything is read from what is already there (collector series in Prometheus,
drift, NetBox); nothing asks a switch.
"""

import asyncio
from dataclasses import dataclass, field

from orchestrator import controlplane, faceplate as fp, hardware, protection, topology
from orchestrator.routing_intent import status_of
from orchestrator.segments import PEER_LINK_TAG, _cf, _ref_id

CRITICAL, MAJOR, MINOR = 40, 20, 10
GOOD, WARNING = 90, 60   # 90+ good, 60-89 warning, under 60 critical
ERRORS_RISING = ("(increase(fabric_interface_in_errors_total[5m]) + increase(fabric_interface_out_errors_total[5m])) > 0")


def level(score: int) -> str:
    return "good" if score >= GOOD else "warning" if score >= WARNING else "critical"


def scored(kind: str, name: str, reasons: list[tuple[int, str]]) -> dict:
    score = max(0, 100 - sum(points for points, _ in reasons))
    return {"kind": kind, "name": name, "score": score, "level": level(score),
            "reasons": [{"points": -points, "text": text} for points, text in sorted(reasons, key=lambda r: -r[0])]}


@dataclass
class Facts:
    """What the scores are made from, for one fabric."""
    devices: dict[str, dict]                                  # name -> NetBox device
    collected: set[str] = field(default_factory=set)          # polled recently
    oper: dict[tuple[str, str], bool] = field(default_factory=dict)
    in_use: dict[str, set[str]] = field(default_factory=dict)   # switch -> ports in use and its Eth-Trunks
    expected_down: set[tuple[str, str]] = field(default_factory=set)
    trunk_of: dict[tuple[str, str], str] = field(default_factory=dict)   # (switch, member port) -> its Eth-Trunk
    errors: set[tuple[str, str]] = field(default_factory=set)   # errors rising (5 minutes)
    flapping: dict[tuple[str, str], int] = field(default_factory=dict)   # downs in 30 minutes
    tripped: dict[tuple[str, str], list] = field(default_factory=dict)   # what protection does to a port
    drift: dict[str, str] = field(default_factory=dict)          # switch -> in_sync, out_of_sync, ...
    control: dict[str, dict] = field(default_factory=dict)       # controlplane.shape per switch
    hardware: dict[str, dict] = field(default_factory=dict)      # hardware.shape per switch
    links: list[dict] = field(default_factory=list)              # topology.build links
    segments: list[dict] = field(default_factory=list)
    services: list[dict] = field(default_factory=list)

    def active(self, name: str) -> bool:
        return name in self.devices and status_of(self.devices[name]) == "active"


def switch_health(name: str, f: Facts) -> dict:
    reasons: list[tuple[int, str]] = []
    if name not in f.collected:
        reasons.append((CRITICAL, "not polled: the collector cannot read it"))
    if f.drift.get(name) == "out_of_sync":
        reasons.append((MINOR, "out of sync with its intent"))
    ports = sorted(f.in_use.get(name, set()), key=topology.port_key)
    down = {p for p in ports if f.oper.get((name, p)) is False and (name, p) not in f.expected_down}
    for port in ports:
        trunk = f.trunk_of.get((name, port))
        flaps = f.flapping.get((name, port))
        if port in down and trunk not in down:
            members = [m for m in ports if m in down and f.trunk_of.get((name, m)) == port]
            notes = ([f"members {', '.join(members)}"] if members else []) + ([f"flapping: {flaps} downs in 30 min"] if flaps else [])
            reasons.append((MAJOR, f"{port} is down" + (f" ({'; '.join(notes)})" if notes else "")))
        elif flaps and port not in down:
            reasons.append((MAJOR, f"{port} is flapping ({flaps} downs in 30 min)"))
        if (name, port) in f.errors:
            reasons.append((MINOR, f"errors rising on {port}"))
        for trip in f.tripped.get((name, port), ()):
            reasons.append((MINOR, protection.trip_text(port, trip)))
    plane = f.control.get(name) or {}
    for peer in plane.get("bgp", []):
        if not peer["up"] and not peer["expected_down"]:
            who = f" ({peer['switch']})" if peer.get("switch") else ""
            reasons.append((MAJOR, f"BGP peer {peer['peer']}{who}, {peer['family']}: {peer['state']}"))
    for neighbour in plane.get("ospf", []):
        if not neighbour["up"]:
            reasons.append((MAJOR, f"OSPF neighbour {neighbour['neighbor']} on {neighbour['interface']}: {neighbour['state']}"))
    mlag = plane.get("mlag")
    if mlag and not mlag["expected_down"]:
        if mlag["heartbeat"] is False:
            reasons.append((CRITICAL, f"M-LAG heartbeat with {mlag['partner'] or 'the peer'} lost"))
        for link, up in sorted(mlag["peer_links"].items()):
            if not up:
                reasons.append((CRITICAL, f"M-LAG peer-link {link} down"))
    hw = f.hardware.get(name) or {}
    for p in hw.get("power", []):
        if not p["ok"]:
            reasons.append((CRITICAL, f"power module {p['module']} is {p['status'] or 'not normal'}"))
    for fan in hw.get("fans", []):
        if not fan["present"]:
            reasons.append((MAJOR, f"fan {fan['fan']} missing"))
    for s in hw.get("sensors", []):
        t, where = s["thresholds"], f"temperature {s['sensor']} (slot {s['slot']}) at {s['celsius']:g} °C"
        if "major" in t and s["celsius"] >= t["major"]:
            reasons.append((MAJOR, f"{where}, at its Major threshold"))
        elif ("minor" in t and s["celsius"] >= t["minor"]) or s["normal"] is False:
            reasons.append((MINOR, f"{where}, at its Minor threshold" if "minor" in t and s["celsius"] >= t["minor"]
                            else f"{where}, not Normal"))
    for kind in ("cpu", "memory"):
        for row in hw.get(kind, []):
            if not row["ok"]:
                reasons.append((MINOR, f"{'CPU' if kind == 'cpu' else 'memory'} of slot {row['slot']} at {row['usage']:g} %"))
    for optic in hw.get("optics", []):
        if optic["level"] in ("alarm", "warning"):
            reasons.append((MAJOR if optic["level"] == "alarm" else MINOR,
                            f"optic in {optic['port']}: " + "; ".join(optic["reasons"])))
    return scored("switch", name, reasons)


def link_health(link: dict, f: Facts) -> dict | None:
    """A cable between two fabric switches; None when an end is not an active fabric switch (planned: expected down)."""
    ends = link["ends"]
    if link.get("source") != "netbox" or not all(f.active(e["device"]) for e in ends):
        return None
    reasons: list[tuple[int, str]] = []
    down = [e for e in ends if e["oper_up"] is False]
    for e in down:
        flaps = f.flapping.get((e["device"], e["interface"]))
        reasons.append((MAJOR, f"{e['device']} {e['interface']} is down" + (f" (flapping: {flaps} downs in 30 min)" if flaps else "")))
    for e in ends:
        if e["oper_up"] is not False and (flaps := f.flapping.get((e["device"], e["interface"]))):
            reasons.append((MAJOR, f"{e['device']} {e['interface']} is flapping ({flaps} downs in 30 min)"))
    for e in ends:
        if e["check"] == "mismatch":
            reasons.append((MINOR, f"{e['device']} {e['interface']} sees another neighbour over LLDP"))
        elif e["check"] == "missing" and not down:
            reasons.append((MINOR, f"{e['device']} {e['interface']} sees no LLDP neighbour"))
        if (e["device"], e["interface"]) in f.errors:
            reasons.append((MINOR, f"errors rising on {e['device']} {e['interface']}"))
    a, b = ends
    return scored("link", f"{a['device']} {a['interface']} – {b['device']} {b['interface']}", reasons)


def segment_health(segment: dict, f: Facts) -> dict:
    reasons: list[tuple[int, str]] = []
    unpolled = set()
    for a in segment.get("attachments") or []:
        device, port = a["device"], a["interface"]
        if not f.active(device):
            continue
        if device not in f.collected:
            unpolled.add(device)
        elif f.oper.get((device, port)) is False:
            reasons.append((MAJOR, f"{device} {port} is down"))
    firewall = (segment.get("routing") or {}).get("firewall")
    for service in f.services:
        if service["name"] != firewall:
            continue
        for device in service.get("devices") or [service["device"]]:
            if not f.active(device):
                continue
            if device not in f.collected:
                unpolled.add(device)
            elif f.oper.get((device, service["port"])) is False:
                reasons.append((MAJOR, f"service {firewall}: {device} {service['port']} is down"))
    reasons += [(CRITICAL, f"{d} is not polled") for d in sorted(unpolled)]
    return scored("segment", segment["name"], reasons)


def fabric_health(fabric: str, f: Facts) -> dict:
    switches = [switch_health(n, f) for n in sorted(f.devices) if f.active(n)]
    links = [h for h in (link_health(link, f) for link in f.links) if h is not None]
    segments = [segment_health(s, f) for s in sorted(f.segments, key=lambda s: s["name"])]
    ranked = sorted(switches + links, key=lambda h: (h["score"], h["kind"], h["name"]))
    worst = ranked[0] if ranked else None
    others = sum(1 for h in ranked[1:] if h["score"] < GOOD)
    score = worst["score"] if worst else 100
    return {"fabric": fabric, "score": score, "level": level(score),
            "worst": {"kind": worst["kind"], "name": worst["name"]} if worst and worst["reasons"] else None,
            "reasons": worst["reasons"] if worst else [], "others_below_good": others,
            "switches": switches, "links": links, "segments": segments,
            "planned": sorted(n for n in f.devices if not f.active(n))}


# -- from NetBox, Prometheus and drift -------------------------------------------------------------

async def facts(nb, prometheus, fabric: str, drift_report: list[dict], max_age: float) -> Facts:
    """Everything the scores need for one fabric (a few NetBox reads and Prometheus queries)."""
    from orchestrator.routing_intent import RoutingStore

    devices_list, interfaces, (services, segments) = await asyncio.gather(
        nb.find("dcim/devices", site=fabric), nb.find("dcim/interfaces", site=fabric),
        RoutingStore(nb).fabric_state(fabric))
    devices = {d["name"]: d for d in devices_list}
    active = sorted(n for n, d in devices.items() if status_of(d) == "active")
    f = Facts(devices=devices, segments=segments, services=services,
              drift={r["device"]: r["state"] for r in drift_report})
    by_id = {d["id"]: d for d in devices_list}
    cables = []
    for i in interfaces:
        device = (i.get("device") or {}).get("name")
        if i.get("lag"):
            f.trunk_of[(device, i["name"])] = i["lag"].get("name") if isinstance(i["lag"], dict) else None
        if (i.get("type") or {}).get("value") == "lag":
            f.in_use.setdefault(device, set()).add(i["name"])
            peer = by_id.get(_ref_id(_cf(by_id.get(_ref_id(i.get("device")), {}), "mlag_peer")))
            if PEER_LINK_TAG in fp._tags(i) and peer is not None and status_of(peer) != "active":
                f.expected_down.add((device, i["name"]))
        if i.get("link_peers_type") == "dcim.interface":
            for far in i.get("link_peers") or []:
                far_device = (far.get("device") or {}).get("name")
                cables.append(((device, i["name"]), (far_device, far["name"])))
                if far_device not in devices or status_of(devices[far_device]) != "active":
                    f.expected_down.add((device, i["name"]))
    if active:
        for device, ports in (await fp.ports_in_use(nb, active)).items():
            f.in_use.setdefault(device, set()).update(ports)
    if prometheus is not None and active:
        match = prometheus._devices_match(active)
        telemetry, control_series, hardware_series, errors, flaps, f.tripped = await asyncio.gather(
            prometheus.telemetry(max_age),
            prometheus.query(f'{{__name__=~"{controlplane.SERIES}",{match}}}'),
            prometheus.query(f'{{__name__=~"{hardware.SERIES}",{match}}}'),
            prometheus.query(ERRORS_RISING),
            prometheus.query(f"fabric_link_flapping{{{match}}}"), protection.tripped(prometheus, active))
        f.collected = telemetry.collected & set(active)
        f.oper = telemetry.oper_up
        f.errors = {(m["device"], m["interface"]) for m, _, _ in errors if m.get("device") in devices}
        f.flapping = {(m["device"], m["interface"]): int(v) for m, _, v in flaps if m.get("interface")}
        owner_of, _ = await controlplane.owners(nb, fabric)
        per_device: dict[str, list] = {}
        for item in control_series:
            per_device.setdefault(item[0].get("device", ""), []).append(item)
        f.control = {n: controlplane.shape(n, per_device.get(n, []), owner_of, devices) for n in active}
        per_device = {}
        for item in hardware_series:
            per_device.setdefault(item[0].get("device", ""), []).append(item)
        f.hardware = {n: hardware.shape(n, per_device.get(n, []), f.in_use.get(n, set())) for n in active}
        nodes = [topology.Device(n, fabric, None, status_of(d)) for n, d in devices.items()]
        f.links = topology.build(nodes, cables, telemetry)["links"]
    return f
