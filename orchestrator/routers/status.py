"""Health, switch lists and pages, topology, overview, traffic, control plane, hardware."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Response

from orchestrator.annet import AnnetError, AnnetRunner
from orchestrator import hostkeys, segments as segs
from orchestrator.access import Access
from orchestrator import inventory as inv, locations as loc
from orchestrator.redact import redact
from orchestrator.netbox import ANNET_SUPPORTED_MINOR_VERSIONS, NetBoxClient, NetBoxError
from orchestrator.prometheus import PrometheusClient, PrometheusError
from orchestrator import topology
from orchestrator.deps import (
    COLLECTED_MAX_AGE, get_access, get_annet, get_netbox, get_prometheus, LOGIN, _slug,
)
from orchestrator.answers import Device, Health


router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/health", response_model=Health, response_model_exclude_unset=True)
async def health(response: Response, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    try:
        status = await netbox.status()
    except NetBoxError as exc:   # /health needs no login: the details go to the log only
        log.warning("health: %s", exc)
        response.status_code = 503
        return {"status": "error", "netbox": {"ok": False, "error": "NetBox is unreachable or refused the request"}}

    if status.minor_version not in ANNET_SUPPORTED_MINOR_VERSIONS:
        response.status_code = 503
        return {
            "status": "error",
            "netbox": {
                "ok": False,
                "version": status.version,
                "error": f"NetBox {status.version} is not supported by Annet's NetBox adapter",
            },
        }

    return {
        "status": "ok",
        "netbox": {
            "ok": True,
            "version": status.version,
            "rq_workers_running": status.rq_workers_running,
        },
    }


@router.get("/devices/{name}/config", dependencies=[LOGIN])
async def device_config(
    name: str,
    netbox: NetBoxClient = Depends(get_netbox),
    annet: AnnetRunner = Depends(get_annet),
) -> dict:
    """Generated config for one device (`annet gen`). Read-only: nothing is fetched or deployed."""
    try:
        device = await netbox.find_one("dcim/devices", name=name)
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if device is None:
        raise HTTPException(status_code=404, detail=f"device {name!r} not found in NetBox")
    try:
        config = await annet.gen(name)
    except AnnetError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"device": name, "config": redact(config)}


@router.get("/devices", dependencies=[LOGIN], response_model=list[Device], response_model_exclude_unset=True)
async def devices(site: str | None = None, netbox: NetBoxClient = Depends(get_netbox),
                  access: Access = Depends(get_access)) -> list[dict]:
    """Devices from NetBox, optionally for one fabric (site slug)."""
    try:
        found = await netbox.find("dcim/devices", **({"site": site} if site else {}))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    names = {d["id"]: d["name"] for d in found}
    return sorted(
        (
            {
                "name": d["name"],
                "site": _slug(d.get("site")),
                "role": _slug(d.get("role")),
                "status": _slug(d.get("status")),
                "primary_ip": (d.get("primary_ip4") or {}).get("address"),
                **loc.placement(d),
                # Host keys pinned: nothing connects to an active device until they are.
                "pinned": bool((d.get("custom_fields") or {}).get(hostkeys.FIELD)),
                "meth_mac": (d.get("custom_fields") or {}).get(inv.MAC_FIELD),
                # M-LAG peer (the Ports page asks for both switches at once)
                "peer": names.get(((d.get("custom_fields") or {}).get("mlag_peer") or {}).get("id")),
            }
            for d in found if _slug(d.get("site")) != inv.SITE   # not in a fabric
            and access.sees(_slug(d.get("site")))
        ),
        key=lambda d: d["name"],
    )


@router.get("/topology", dependencies=[LOGIN])
async def get_topology(
    site: str | None = None,
    netbox: NetBoxClient = Depends(get_netbox),
    prometheus: PrometheusClient | None = Depends(get_prometheus),
) -> dict:
    """NetBox cabling checked against LLDP (see orchestrator/topology.py)."""
    scope = {"site": site} if site else {}
    try:
        # One fabric: its whole interface list, the read health and the event watcher already share
        #, cut to the cabled ones here.
        found, interfaces = await asyncio.gather(
            netbox.find("dcim/devices", **scope),
            netbox.find("dcim/interfaces", **scope) if site else netbox.find("dcim/interfaces", cabled="true"))
        interfaces = [i for i in interfaces if i.get("cable")]
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    devices = [
        topology.Device(
            name=d["name"], site=_slug(d.get("site")), role=_slug(d.get("role")), status=_slug(d.get("status"))
        )
        for d in found if _slug(d.get("site")) != inv.SITE
    ]
    cables = [
        ((i["device"]["name"], i["name"]), (peer["device"]["name"], peer["name"]))
        for i in interfaces
        if i.get("link_peers_type") == "dcim.interface"
        for peer in i.get("link_peers") or []
    ]

    telemetry_status = {"ok": False, "error": "Prometheus is not configured"}
    telemetry = topology.Telemetry()
    if prometheus is not None:
        try:
            telemetry = await prometheus.telemetry(COLLECTED_MAX_AGE)
            telemetry_status = {"ok": True, "error": None}
        except PrometheusError as exc:
            telemetry_status = {"ok": False, "error": str(exc)}

    return {**topology.build(devices, cables, telemetry), "telemetry": telemetry_status}


@router.get("/devices/{name}/interfaces", dependencies=[LOGIN])
async def device_interfaces(name: str, prometheus: PrometheusClient | None = Depends(get_prometheus)) -> dict:
    """Per interface: oper status, traffic in bits/s (last two samples, physical ports), errors and
    discards (last 5 minutes), from the collector's counters in Prometheus."""
    if prometheus is None:
        raise HTTPException(status_code=503, detail="Prometheus is not configured")
    try:
        _, _, oper = await prometheus.device_state(name)
        traffic = await prometheus.interface_traffic(name)
    except PrometheusError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    names = sorted(set(oper) | set(traffic), key=topology.port_key)
    return {
        "device": name,
        "interfaces": [{"interface": n, "oper_up": oper.get(n), **traffic.get(n, {})} for n in names],
    }


@router.get("/overview", dependencies=[LOGIN])
async def overview(site: str | None = None, netbox: NetBoxClient = Depends(get_netbox),
                   prometheus: PrometheusClient | None = Depends(get_prometheus)) -> dict:
    """What only Prometheus knows, for the overview page: alerts firing now and the busiest
    physical ports of the fabric's active devices, with 15 minutes of history. Devices, drift and
    deploys come from their own endpoints."""
    try:
        found = await netbox.find("dcim/devices", **({"site": site} if site else {}))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    names = {d["name"] for d in found}
    active = sorted(d["name"] for d in found if _slug(d.get("status")) == "active")
    if prometheus is None:
        return {"alerts": [], "ports": [], "control": [], "telemetry": {"ok": False, "error": "Prometheus is not configured"}}
    from orchestrator import controlplane

    try:
        alerts = [a for a in await prometheus.firing_alerts() if a["device"] is None or a["device"] in names]
        ports = await prometheus.busiest_ports(active)
        # Control-plane problems: peers down, heartbeat lost, planned partners aside.
        control = [p for p in await controlplane.fabric_problems(netbox, prometheus, site) if p["device"] in names]
    except PrometheusError as exc:
        return {"alerts": [], "ports": [], "control": [], "telemetry": {"ok": False, "error": str(exc)}}
    return {"alerts": alerts, "ports": ports, "control": control, "telemetry": {"ok": True, "error": None}}


@router.get("/traffic", dependencies=[LOGIN])
async def fabric_traffic(site: str | None = None, netbox: NetBoxClient = Depends(get_netbox),
                         prometheus: PrometheusClient | None = Depends(get_prometheus)) -> dict:
    """In/out bits per second now on every physical port of the fabric's active devices, for the
    topology map's traffic view."""
    try:
        found = await netbox.find("dcim/devices", **({"site": site} if site else {}))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    active = sorted(d["name"] for d in found if _slug(d.get("status")) == "active")
    if prometheus is None:
        return {"interfaces": {}, "telemetry": {"ok": False, "error": "Prometheus is not configured"}}
    try:
        return {"interfaces": await prometheus.fabric_traffic(active), "telemetry": {"ok": True, "error": None}}
    except PrometheusError as exc:
        return {"interfaces": {}, "telemetry": {"ok": False, "error": str(exc)}}


@router.get("/devices/{name}/control-plane", dependencies=[LOGIN])
async def device_control_plane(name: str, netbox: NetBoxClient = Depends(get_netbox),
                               prometheus: PrometheusClient | None = Depends(get_prometheus)) -> dict:
    """BGP and EVPN peers, OSPF neighbours, M-LAG, VXLAN tunnels, route and MAC counts of one switch,
    as the collector last read them (every 30 s)."""
    from orchestrator import controlplane

    if prometheus is None:
        raise HTTPException(status_code=503, detail="Prometheus is not configured")
    try:
        return await controlplane.control_plane(netbox, prometheus, name)
    except PrometheusError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/devices/{name}/hardware", dependencies=[LOGIN])
async def device_hardware(name: str, netbox: NetBoxClient = Depends(get_netbox),
                          prometheus: PrometheusClient | None = Depends(get_prometheus)) -> dict:
    """Optics, fans, power, CPU, memory and temperatures of one switch, as the collector last read them
    (each group every 50 s), with problems; an optic's level counts only on a port in use."""
    from orchestrator import hardware

    if prometheus is None:
        raise HTTPException(status_code=503, detail="Prometheus is not configured")
    try:
        return await hardware.device_hardware(netbox, prometheus, name)
    except PrometheusError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/devices/{name}/history", dependencies=[LOGIN])
async def interface_history(name: str, interface: str, minutes: int = 60,
                            prometheus: PrometheusClient | None = Depends(get_prometheus)) -> dict:
    """One physical port's traffic over the last `minutes` (15 to 1440), for the device page's chart."""
    if prometheus is None:
        raise HTTPException(status_code=503, detail="Prometheus is not configured")
    if not 15 <= minutes <= 1440:
        raise HTTPException(status_code=400, detail="minutes is 15 to 1440")
    try:
        return {"device": name, "interface": interface, "minutes": minutes,
                **await prometheus.interface_history(name, interface, minutes)}
    except PrometheusError as exc:
        status = 400 if "invalid" in str(exc) else 503
        raise HTTPException(status_code=status, detail=str(exc))
