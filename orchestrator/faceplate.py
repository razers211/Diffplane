"""A switch's front panel for the GUI: every front-panel port and what it
carries, so ports are picked by clicking a drawing instead of typing names. Read only: it only
reads what NetBox already holds. Link up/down comes from the collector (`/devices/<name>/interfaces`).

Kinds, the first that applies:
  fabric   routed link to another fabric switch (an address, or the `unnumbered` tag)
  peer     member of the M-LAG peer-link
  trunk    member of another Eth-Trunk (detail: the trunk; `mlag` when it is an M-LAG trunk)
  service  the port of a firewall or ISP service
  segment  carries segments (attachments on the port itself)
  free     none of these

A port is **in use** (only there is an optic's level a problem) when it is not
free or has a cable in NetBox.
"""

import asyncio
import re
import time

from orchestrator.segments import MLAG_TAG, PEER_LINK_TAG, UNNUMBERED_TAG, SegmentError, _cf, _ref_id

FRONT_PANEL = re.compile(r"\d*GE\d+/\d+/\d+$")   # 10GE1/0/4, 100GE1/0/1; never MEth, never .N


def _tags(obj: dict) -> set[str]:
    return {t.get("slug") for t in obj.get("tags") or [] if isinstance(t, dict)}


async def faceplate(nb, name: str) -> dict:
    device = await nb.find_one("dcim/devices", name=name)
    if device is None:
        raise SegmentError(f"no device {name!r}")
    site = _ref_id(device.get("site"))
    interfaces, addresses, vrfs = await asyncio.gather(
        nb.find("dcim/interfaces", device_id=device["id"]),
        nb.find("ipam/ip-addresses", device_id=device["id"]),
        nb.find("ipam/vrfs"))
    routed = {ip.get("assigned_object_id") for ip in addresses}
    by_id = {i["id"]: i for i in interfaces}
    peer = _ref_id(_cf(device, "mlag_peer"))
    services = {}
    for v in vrfs:
        if _ref_id(_cf(v, "fabric")) != site or not _cf(v, "service_device"):
            continue
        owner = _ref_id(_cf(v, "service_device"))
        # A service on an M-LAG pair (per-leaf addresses) holds its trunk on both leaves.
        if owner == device["id"] or (_cf(v, "service_addresses") and peer is not None and owner == peer):
            services[_cf(v, "service_port")] = v["name"].split(":", 1)[-1]   # stored as "<fabric>:<name>"

    ports = []
    for iface in interfaces:
        if not FRONT_PANEL.fullmatch(iface["name"]) or (iface.get("type") or {}).get("value") in ("lag", "virtual"):
            continue
        lag = by_id.get(_ref_id(iface.get("lag"))) if iface.get("lag") else None
        far = next(iter(iface.get("link_peers") or []), None)
        detail, trunk = None, None
        if iface["id"] in routed or UNNUMBERED_TAG in _tags(iface):
            kind = "fabric"
            if far and far.get("device"):
                detail = f"to {far['device'].get('name')} {far.get('name')}"
        elif lag is not None and PEER_LINK_TAG in _tags(lag):
            kind, trunk = "peer", lag["name"]
            detail = f"peer-link {lag['name']}"
        elif lag is not None:
            kind, trunk = "trunk", lag["name"]
            detail = lag["name"] + (" (M-LAG)" if MLAG_TAG in _tags(lag) else "")
            if lag["name"] in services:
                detail += f", service {services[lag['name']]}"
            elif _cf(lag, "segments"):
                detail += f", {len(_cf(lag, 'segments'))} segment(s)"
        elif iface["name"] in services:
            kind, detail = "service", f"service {services[iface['name']]}"
        elif _cf(iface, "segments"):
            kind = "segment"
            detail = f"{len(_cf(iface, 'segments'))} segment(s)"
        else:
            kind = "free"
        cabled = bool(iface.get("cable"))
        ports.append({"name": iface["name"], "kind": kind, "detail": detail, "trunk": trunk,
                      "segments": _cf(iface, "segments") or [], "cabled": cabled,
                      "off": iface.get("enabled") is False, "description": iface.get("description") or "",
                      "in_use": kind != "free" or cabled})
    model = ((device.get("device_type") or {}).get("model"))
    return {"device": name, "model": model, "ports": ports}


_IN_USE: dict[str, tuple[float, set[str]]] = {}   # switch -> (when read, its ports in use)
IN_USE_SECONDS = 120


async def ports_in_use(nb, devices: list[str]) -> dict[str, set[str]]:
    """switch -> its front-panel ports in use, for the orchestrator's /metrics (alert rules keep
    optic levels to these ports) and the health scores. Kept 2 minutes per switch: Prometheus
    scrapes every 30 s, NetBox costs 3 reads per switch. shortcut: a port newly in use alarms up to
    2 minutes late; drop the cache if that matters."""
    now = time.monotonic()
    stale = [n for n in devices if n not in _IN_USE or now - _IN_USE[n][0] >= IN_USE_SECONDS]
    for plate in await asyncio.gather(*(faceplate(nb, name) for name in stale)):
        _IN_USE[plate["device"]] = (now, {p["name"] for p in plate["ports"] if p["in_use"]})
    return {n: _IN_USE[n][1] for n in devices}


def in_use_metrics(found: dict[str, set[str]]) -> bytes:
    """`fabric_port_in_use{device,interface}` 1 for each port in use (alert rules join on it)."""
    from prometheus_client import CollectorRegistry, Gauge, generate_latest

    registry = CollectorRegistry()
    gauge = Gauge("fabric_port_in_use", "1 for a front-panel port in use: cabled, trunk member, service port or "
                  "carrying segments", ["device", "interface"], registry=registry)
    for device, ports in sorted(found.items()):
        for port in sorted(ports):
            gauge.labels(device, port).set(1)
    return generate_latest(registry)
