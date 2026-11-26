"""Segments, their port attachments, and services."""

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from orchestrator import routing as rt, segments as segs
from orchestrator import faceplate as fp
from orchestrator.auth import Accounts
from orchestrator.netbox import NetBoxClient, NetBoxError
from orchestrator.deps import (
    current_user, get_accounts, get_netbox, get_routing, get_segments, INTENT_WRITE, LOGIN,
    _segment_call,
)
from orchestrator.answers import Segment, Service


router = APIRouter()


@router.get("/segments", dependencies=[LOGIN], response_model=list[Segment], response_model_exclude_unset=True)
async def list_segments(site: str, segments: segs.Segments = Depends(get_segments)) -> list[dict]:
    """Segments of one fabric (site slug), plus unassigned ones from before fabrics."""
    try:
        return await segments.all(site)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/devices/{name}/ports", dependencies=[LOGIN])
async def attachable_ports(name: str, services: bool = False,
                           segments: segs.Segments = Depends(get_segments)) -> list[dict]:
    """Ports of a leaf that may carry segments (server trunks first); `services`: also those
    services sit on (a new service may join them)."""
    try:
        return await segments.ports(name, with_services=services)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/devices/{name}/faceplate", dependencies=[LOGIN])
async def device_faceplate(name: str, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """Every front-panel port and what it carries, for the drawn front panel. Read only."""
    try:
        return await fp.faceplate(netbox, name)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/segments/next-bd-id", dependencies=[LOGIN])
async def next_bd_id(site: str, segments: segs.Segments = Depends(get_segments)) -> dict:
    try:
        return {"bd_id": await segments.next_bd_id(site)}
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/segments", status_code=201, dependencies=INTENT_WRITE)
async def create_segment(
    site: str = Body(), name: str = Body(), vni: int = Body(), bd_id: int = Body(),
    user: str = Depends(current_user), segments: segs.Segments = Depends(get_segments),
    accounts: Accounts = Depends(get_accounts),
) -> dict:
    return await _segment_call(segments.create(site, name, vni, bd_id), accounts, user, "segment.create",
                               f"{site}:{name} vni={vni} bd={bd_id}")


@router.patch("/segments/{site}/{vni}", dependencies=INTENT_WRITE)
async def rename_segment(
    site: str, vni: int, name: str = Body(embed=True), user: str = Depends(current_user),
    segments: segs.Segments = Depends(get_segments), accounts: Accounts = Depends(get_accounts),
) -> dict:
    """Rename a segment; a routed one's VPN instance is replaced at its next deploy."""
    return await _segment_call(segments.rename(site, vni, name), accounts, user, "segment.rename",
                               f"{site} vni={vni} name={name}")


@router.delete("/segments/{site}/{vni}", dependencies=INTENT_WRITE)
async def delete_segment(
    site: str, vni: int, user: str = Depends(current_user), segments: segs.Segments = Depends(get_segments),
    accounts: Accounts = Depends(get_accounts),
) -> dict:
    await _segment_call(segments.delete(site, vni), accounts, user, "segment.delete", f"{site} vni={vni}")
    return {}


@router.post("/segments/{site}/{vni}/attachments", status_code=201, dependencies=INTENT_WRITE)
async def attach_segment(
    site: str, vni: int, device: str = Body(), interface: str = Body(), vid: int | None = Body(default=None),
    user: str = Depends(current_user), segments: segs.Segments = Depends(get_segments),
    accounts: Accounts = Depends(get_accounts),
) -> dict:
    ports = await _segment_call(segments.attach(site, device, interface, vni, vid), accounts, user,
                                "segment.attach",
                                f"{site} vni={vni} {device} {interface} vid={vid if vid is not None else 'untagged'}")
    return {"ports": ports}


@router.patch("/segments/{site}/{vni}/attachments", dependencies=INTENT_WRITE)
async def change_attachment(
    site: str, vni: int, device: str = Body(), interface: str = Body(), vid: int | None = Body(default=None),
    user: str = Depends(current_user), segments: segs.Segments = Depends(get_segments),
    accounts: Accounts = Depends(get_accounts),
) -> dict:
    ports = await _segment_call(segments.set_vid(site, device, interface, vni, vid), accounts, user,
                                "segment.change",
                                f"{site} vni={vni} {device} {interface} vid={vid if vid is not None else 'untagged'}")
    return {"ports": ports}


@router.delete("/segments/{site}/{vni}/attachments", dependencies=INTENT_WRITE)
async def detach_segment(
    site: str, vni: int, device: str, interface: str,
    user: str = Depends(current_user), segments: segs.Segments = Depends(get_segments),
    accounts: Accounts = Depends(get_accounts),
) -> dict:
    ports = await _segment_call(segments.detach(site, device, interface, vni), accounts, user, "segment.detach",
                                f"{site} vni={vni} {device} {interface}")
    return {"ports": ports}


# -- routing: firewall services and routed segments ---------------------------------

@router.get("/services", dependencies=[LOGIN], response_model=list[Service], response_model_exclude_unset=True)
async def list_services(site: str, routing: rt.Routing = Depends(get_routing)) -> list[dict]:
    """Firewall services of one fabric; the first is the default for new routed segments."""
    try:
        return await routing.services(site)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/services", status_code=201, dependencies=INTENT_WRITE)
async def create_service(
    site: str = Body(), name: str = Body(), device: str = Body(), port: str = Body(), vlan: int = Body(),
    address: str = Body(), next_hop: str = Body(), asn: int | None = Body(default=None),
    kind: str = Body(default="firewall"), addresses: dict[str, str] | None = Body(default=None),
    ipv6: dict | None = Body(default=None), vrf: str | None = Body(default=None),
    user: str = Depends(current_user), routing: rt.Routing = Depends(get_routing),
    accounts: Accounts = Depends(get_accounts),
) -> dict:
    """On an M-LAG pair's server trunk: `address` is the Vlanif subnet, or the shared M-LAG
    address with each leaf's address in `addresses`. `vrf`: an existing VPN instance to join
    (another service's, or a routed segment's); none: its own."""
    return await _segment_call(routing.create_service(site, name, device, port, vlan, address, next_hop, asn, kind,
                                                      addresses, ipv6, vrf),
                               accounts, user, "service.create",
                               f"{site}:{name} {kind} {device} {port} vlan={vlan} {address} next-hop={next_hop} "
                               f"asn={asn}" + (f" addresses={addresses}" if addresses else "")
                               + (f" ipv6={ipv6}" if ipv6 else "") + (f" vrf={vrf}" if vrf else ""))


class ServiceChange(BaseModel):
    asn: int | None   # null: no BGP session with the firewall


@router.patch("/services/{site}/{name}", dependencies=INTENT_WRITE)
async def change_service(
    site: str, name: str, change: ServiceChange, user: str = Depends(current_user),
    routing: rt.Routing = Depends(get_routing), accounts: Accounts = Depends(get_accounts),
) -> dict:
    return await _segment_call(routing.set_service_asn(site, name, change.asn), accounts, user, "service.change",
                               f"{site}:{name} asn={change.asn}")


class ServiceIpv6(BaseModel):
    address: str                            # the subnet, or the address (a pair's shared M-LAG one)
    next_hop: str
    addresses: dict[str, str] | None = None   # a pair service typed by hand: each leaf's address


@router.put("/services/{site}/{name}/ipv6", dependencies=INTENT_WRITE)
async def set_service_ipv6(
    site: str, name: str, ipv6: ServiceIpv6 | None = Body(default=None), user: str = Depends(current_user),
    routing: rt.Routing = Depends(get_routing), accounts: Accounts = Depends(get_accounts),
) -> dict:
    """A service's IPv6: its Vlanif subnet or address and the next hop; null takes it away."""
    value = ipv6.model_dump(exclude_none=True) if ipv6 else None
    return await _segment_call(routing.set_service_ipv6(site, name, value), accounts, user, "service.ipv6",
                               f"{site}:{name} ipv6={value}")


@router.delete("/services/{site}/{name}", dependencies=INTENT_WRITE)
async def delete_service(
    site: str, name: str, user: str = Depends(current_user), routing: rt.Routing = Depends(get_routing),
    accounts: Accounts = Depends(get_accounts),
) -> dict:
    await _segment_call(routing.delete_service(site, name), accounts, user, "service.delete", f"{site}:{name}")
    return {}
