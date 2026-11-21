"""A switch's control-plane state for the GUI: what the collector put in Prometheus, shaped
per switch, with each BGP peer named after the fabric switch that owns its address. A peer or M-LAG
partner that is planned is expected to be down and is not a problem.
"""

import asyncio
import re
import time

from orchestrator.prometheus import PrometheusError
from orchestrator.routing_intent import status_of
from orchestrator.segments import _cf, _ref_id

DEVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SERIES = 'fabric_(bgp_peer_.*|ospf_neighbor_info|routes|mlag_.*|vxlan_tunnel_up|mac_entries|control_query_refused)'


async def owners(nb, fabric: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """(address -> its switch, switch name -> switch) for the fabric's addresses (loopbacks, links)."""
    vrf, devices, interfaces = await asyncio.gather(nb.find_one("ipam/vrfs", name=f"fabric-{fabric}"),
                                                    nb.find("dcim/devices", site=fabric),
                                                    nb.find("dcim/interfaces", site=fabric))
    by_id = {d["id"]: d for d in devices}
    iface_device = {i["id"]: by_id.get(_ref_id(i.get("device"))) for i in interfaces}
    addresses = await nb.find("ipam/ip-addresses", vrf_id=vrf["id"]) if vrf else []
    found = {}
    for ip in addresses:
        device = iface_device.get(_ref_id(ip.get("assigned_object_id")))
        if device is not None:
            found[ip["address"].split("/")[0]] = device
    return found, {d["name"]: d for d in devices}


def shape(device: str, series: list[tuple[dict, float, float]], owner_of: dict[str, dict],
          devices: dict[str, dict]) -> dict:
    """The collector's series of one switch, as the GUI shows them, with problems listed."""
    by_name: dict[str, list[tuple[dict, float]]] = {}
    for labels, _, value in series:
        by_name.setdefault(labels.get("__name__", ""), []).append((labels, value))

    def peer_key(m):
        return (m.get("vpn"), m.get("peer"), m.get("family"))

    seconds = {peer_key(m): v for m, v in by_name.get("fabric_bgp_peer_state_seconds", [])}
    prefixes: dict[tuple, dict[str, int]] = {}
    for m, v in by_name.get("fabric_bgp_peer_prefixes", []):
        prefixes.setdefault(peer_key(m), {})[m.get("kind")] = int(v)
    me = devices.get(device) or {}
    partner_id = _ref_id(_cf(me, "mlag_peer"))
    partner = next((d for d in devices.values() if d["id"] == partner_id), None) if partner_id else None

    bgp, problems = [], []
    for m, _ in sorted(by_name.get("fabric_bgp_peer_info", []), key=lambda x: peer_key(x[0])):
        owner = owner_of.get(m.get("peer"))
        planned = owner is not None and status_of(owner) != "active"
        up = m.get("state") == "established"
        row = {"vpn": m.get("vpn"), "peer": m.get("peer"), "family": m.get("family"), "state": m.get("state"),
               "remote_as": m.get("remote_as"), "up": up, "seconds": seconds.get(peer_key(m)),
               "prefixes": prefixes.get(peer_key(m), {}), "switch": owner["name"] if owner else None,
               "expected_down": planned}
        bgp.append(row)
        if not up and not planned:
            who = f" ({owner['name']})" if owner else ""
            problems.append(f"BGP peer {m.get('peer')}{who}, {m.get('family')}"
                            f"{' in ' + m.get('vpn') if m.get('vpn') != '_public_' else ''}: {m.get('state')}")
    ospf = []
    for m, _ in sorted(by_name.get("fabric_ospf_neighbor_info", []), key=lambda x: (x[0].get("vpn"), x[0].get("interface"))):
        up = m.get("state") == "full"
        ospf.append({"vpn": m.get("vpn"), "area": m.get("area"), "interface": m.get("interface"),
                     "neighbor": m.get("neighbor"), "state": m.get("state"), "up": up,
                     "switch": (owner_of.get(m.get("neighbor")) or {}).get("name")})
        if not up:
            problems.append(f"OSPF neighbour {m.get('neighbor')} on {m.get('interface')}: {m.get('state')}")
    mlag = None
    dfs = by_name.get("fabric_mlag_dfs_info", [])
    if dfs:
        labels = dfs[0][0]
        heartbeat = next((v == 1 for _, v in by_name.get("fabric_mlag_heartbeat_up", [])), None)
        partner_planned = partner is not None and status_of(partner) != "active"
        mlag = {"role": labels.get("role"), "state": labels.get("state"), "causation": labels.get("causation"),
                "heartbeat": heartbeat, "partner": partner["name"] if partner else None, "expected_down": partner_planned,
                "peer_links": {m.get("interface"): v == 1 for m, v in by_name.get("fabric_mlag_peer_link_up", [])},
                "ports": {m.get("interface"): v == 1 for m, v in by_name.get("fabric_mlag_port_up", [])}}
        if heartbeat is False and not partner_planned:
            problems.append(f"M-LAG heartbeat with {partner['name'] if partner else 'the peer'} lost "
                            f"({labels.get('causation')})")
    routes: dict[str, dict[str, dict[str, int]]] = {}
    for m, v in by_name.get("fabric_routes", []):
        routes.setdefault(m.get("vpn"), {}).setdefault(m.get("protocol"), {})[m.get("kind")] = int(v)
    tunnels = [{"source": m.get("source"), "destination": m.get("destination"), "up": v == 1,
                "switch": (owner_of.get(m.get("destination")) or {}).get("name")}
               for m, v in sorted(by_name.get("fabric_vxlan_tunnel_up", []), key=lambda x: x[0].get("destination", ""))]
    return {
        "device": device, "bgp": bgp, "ospf": ospf, "mlag": mlag,
        "routes": {vpn: {p: c for p, c in protocols.items() if c.get("total")} for vpn, protocols in sorted(routes.items())},
        "tunnels": tunnels, "macs": {m.get("kind"): int(v) for m, v in by_name.get("fabric_mac_entries", [])},
        "refused": sorted(m.get("query") for m, _ in by_name.get("fabric_control_query_refused", [])),
        "collected": bool(series), "problems": problems,
    }


async def control_plane(nb, prometheus, device: str) -> dict:
    found = await nb.find_one("dcim/devices", name=device)
    fabric = ((found or {}).get("site") or {}).get("slug")
    if not DEVICE.fullmatch(device):   # it goes into a PromQL string
        raise PrometheusError(f"invalid device name: {device!r}")
    series, (owner_of, devices) = await asyncio.gather(
        prometheus.query(f'{{__name__=~"{SERIES}",device="{device}"}}'), owners(nb, fabric))
    return shape(device, series, owner_of, devices)


async def fabric_problems(nb, prometheus, fabric: str | None) -> list[dict]:
    """Every switch's control-plane problems (for "Needs you"): one Prometheus query, one NetBox read set."""
    series = await prometheus.query(f'{{__name__=~"{SERIES}"}}')
    per_device: dict[str, list] = {}
    for item in series:
        per_device.setdefault(item[0].get("device", ""), []).append(item)
    if not per_device:
        return []
    found = await nb.find("dcim/devices", **({"site": fabric} if fabric else {}))
    sites = sorted({(d.get("site") or {}).get("slug") for d in found if d["name"] in per_device} - {None})
    owner_of, devices = {}, {}
    for site in sites:
        o, d = await owners(nb, site)
        owner_of.update(o)
        devices.update(d)
    return [{"device": name, "problem": text}
            for name in sorted(per_device) if name in devices
            for text in shape(name, per_device[name], owner_of, devices)["problems"]]


_PLANNED: tuple[float, bytes] | None = None   # (when made, exposition)
PLANNED_SECONDS = 120


async def planned_metrics(nb) -> bytes:
    """For the alert rules: what is planned, so a peer or M-LAG partner that is not built yet
    never alarms. `fabric_planned_address{peer}`: every fabric address of a planned switch (BGP peers
    and OSPF neighbours are named by address); `fabric_mlag_peer_planned{device}`: an active switch whose
    M-LAG peer is planned. Kept 2 minutes (Prometheus scrapes every 30 s)."""
    global _PLANNED
    if _PLANNED is not None and time.monotonic() - _PLANNED[0] < PLANNED_SECONDS:
        return _PLANNED[1]
    from prometheus_client import CollectorRegistry, Gauge, generate_latest

    from orchestrator.inventory import SITE as INVENTORY

    devices = await nb.find("dcim/devices")
    by_id = {d["id"]: d for d in devices}
    fabrics = sorted({(d.get("site") or {}).get("slug") for d in devices if status_of(d) == "active"} - {None, INVENTORY})
    registry = CollectorRegistry()
    address = Gauge("fabric_planned_address", "1 for an address of a planned switch: peers there are expected down",
                    ["peer"], registry=registry)
    partner = Gauge("fabric_mlag_peer_planned", "1 for a switch whose M-LAG peer is planned: its M-LAG is expected down",
                    ["device"], registry=registry)
    for fabric in fabrics:
        owner_of, _ = await owners(nb, fabric)
        for ip, device in sorted(owner_of.items()):
            if status_of(device) != "active":
                address.labels(ip).set(1)
    for d in devices:
        peer = by_id.get(_ref_id(_cf(d, "mlag_peer")))
        if status_of(d) == "active" and peer is not None and status_of(peer) != "active":
            partner.labels(d["name"]).set(1)
    _PLANNED = (time.monotonic(), generate_latest(registry))
    return _PLANNED[1]
