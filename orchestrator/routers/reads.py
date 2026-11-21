"""Live reads: flows, logs, path trace, reachability, routing tables and BGP."""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from orchestrator import segments as segs
from orchestrator import routes as rv
from orchestrator import routing_intent
from orchestrator.netbox import NetBoxClient, NetBoxError
from orchestrator.prometheus import PrometheusClient
from orchestrator.deps import (
    get_clickhouse, get_netbox, get_prometheus, get_route_reader, get_routing_store, LOGIN,
)

router = APIRouter()


async def _route_call(call):
    try:
        return await call
    except segs.SegmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


async def _flows_call(call):
    try:
        return await call
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (RuntimeError, httpx.HTTPError) as exc:   # ClickHouse refused or is away
        raise HTTPException(status_code=503, detail=f"flows store: {exc}")


@router.get("/fabrics/{site}/flows", dependencies=[LOGIN])
async def fabric_flows(site: str, minutes: int = 60, kind: str = "edge", clickhouse=Depends(get_clickhouse)) -> dict:
    """Top talkers, conversations, segment pairs and busiest ports of the fabric's NetStream
    samples over the last `minutes`: edge ports by default, `kind=vxlan` for uplinks."""
    from orchestrator import flows_view

    return await _flows_call(flows_view.view(clickhouse, site, minutes, kind))


@router.get("/devices/{name}/flows", dependencies=[LOGIN])
async def device_flows(name: str, minutes: int = 60, kind: str = "edge", clickhouse=Depends(get_clickhouse),
                       netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """The same for one switch's own ports."""
    from orchestrator import flows_view

    device = await netbox.find_one("dcim/devices", name=name)
    if device is None:
        raise HTTPException(status_code=404, detail=f"no device {name!r}")
    return await _flows_call(flows_view.view(clickhouse, (device.get("site") or {}).get("slug"), minutes, kind, name))


@router.get("/devices/{name}/logs", dependencies=[LOGIN])
async def device_logs(name: str, severity: int = 7, text: str = "", hours: int = 24, limit: int = 500,
                      clickhouse=Depends(get_clickhouse)) -> list[dict]:
    """A switch's syslog messages, newest first: at `severity` or worse (0 emergency ...
    7 debugging), containing `text`, over the last `hours` (kept 90 days)."""
    from syslogs import store

    return await _flows_call(store.messages(clickhouse, name, severity, text.strip(), hours, limit))


@router.get("/fabrics/{site}/logs", dependencies=[LOGIN])
async def fabric_logs(site: str, severity: int = 4, text: str = "", hours: int = 24, limit: int = 100,
                      clickhouse=Depends(get_clickhouse)) -> list[dict]:
    """Every switch's syslog messages of a fabric, newest first (the NOC's feed): warnings and
    worse unless asked otherwise."""
    from syslogs import store

    return await _flows_call(store.messages(clickhouse, None, severity, text.strip(), hours, limit, site=site))


@router.get("/fabrics/{site}/trace", dependencies=[LOGIN])
async def path_trace(site: str, source: str, destination: str, netbox: NetBoxClient = Depends(get_netbox),
                     reader: rv.RouteReader = Depends(get_route_reader)) -> dict:
    """The path from `source` to `destination` (IPv4 or MAC) in the fabric: ARP and MAC tables
    of its active switches read now, the rest from intent. Nothing is stored or configured."""
    from orchestrator import trace

    try:
        return await trace.run(netbox, trace.TableReader(reader), site, source, destination)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/fabrics/{site}/reach", dependencies=[LOGIN])
async def reachability(site: str, source: str = Query(alias="from"), destination: str = Query(alias="to"),
                       proto: str | None = None, port: int | None = None, netbox: NetBoxClient = Depends(get_netbox),
                       reader: rv.RouteReader = Depends(get_route_reader),
                       prometheus: PrometheusClient | None = Depends(get_prometheus)) -> dict:
    """Why can't A reach B: the first place traffic stops, or the path both ways. Nothing is
    stored or configured."""
    from orchestrator import reach

    try:
        return await reach.run(netbox, reader, prometheus, site, source, destination, proto, port)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/fabrics/{site}/reach/hosts", dependencies=[LOGIN])
async def reach_hosts(site: str, netbox: NetBoxClient = Depends(get_netbox),
                      reader: rv.RouteReader = Depends(get_route_reader)) -> dict:
    """What the Reachability boxes suggest, from the tables read now."""
    from orchestrator import reach

    try:
        return await reach.hosts(netbox, reader, site)
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/devices/{name}/routes", dependencies=[LOGIN])
async def device_routes(name: str, reader: rv.RouteReader = Depends(get_route_reader)) -> dict:
    """Every instance's IPv4 and IPv6 routing tables, read from the switch now (inactive routes included)."""
    return await _route_call(rv.routing_table(reader, name))


@router.get("/devices/{name}/bgp", dependencies=[LOGIN])
async def device_bgp(name: str, vpn: str | None = None, reader: rv.RouteReader = Depends(get_route_reader),
                     store: routing_intent.RoutingStore = Depends(get_routing_store)) -> dict:
    """The BGP tables and peers (IPv4 and IPv6) of the global table or a VPN instance, read from the switch now."""
    return await _route_call(rv.bgp(reader, store, name, vpn or None))


@router.get("/devices/{name}/bgp/simulation", dependencies=[LOGIN])
async def device_bgp_simulation(name: str, peer: str, vpn: str | None = None,
                                reader: rv.RouteReader = Depends(get_route_reader),
                                store: routing_intent.RoutingStore = Depends(get_routing_store)) -> dict:
    """One peer's routes with and without its policies (simulated; the switch is not changed)."""
    return await _route_call(rv.simulation(reader, store, name, vpn or None, peer))
