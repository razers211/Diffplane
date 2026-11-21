"""Fabric list and settings, deletion, change windows, deploy permission, NTP, overrides."""

import json
import asyncio

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from orchestrator import documents
from orchestrator.annet import AnnetError, AnnetRunner
from orchestrator import db, deploys, segments as segs
from orchestrator import overrides
from orchestrator.access import Access
from orchestrator.platform_settings import PlatformSettings
from orchestrator import fabric_delete, fabric_ntp, routing_intent
from orchestrator.auth import Accounts
from orchestrator.netbox import NetBoxClient, NetBoxError
from orchestrator.deps import (
    ADMIN, APPROVE, current_user, get_access, get_accounts, get_annet, get_netbox, get_routing_store,
    get_settings, INTENT_WRITE, LOGIN, _segment_call,
)

router = APIRouter()


@router.get("/fabrics", dependencies=[LOGIN])
async def list_fabrics(netbox: NetBoxClient = Depends(get_netbox), access: Access = Depends(get_access)) -> list[dict]:
    """Every fabric (a NetBox site with a seeded fabric context), for the GUI's fabric picker."""
    try:
        found, sites = await asyncio.gather(documents.every(netbox), netbox.find("dcim/sites"))
        contexts = {c["name"]: c for c in found}
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    fabrics = []
    for site in sites:
        context = contexts.get(f"fabric-{site['slug']}")
        if context is None or not access.sees(site["slug"]):
            continue
        settings = ((context.get("data") or {}).get("fabric") or {})
        fabrics.append({"site": site["slug"], "name": site.get("name") or site["slug"],
                        "architecture": settings.get("architecture") or "symmetric"})
    return sorted(fabrics, key=lambda f: f["site"])


@router.get("/fabrics/{site}", dependencies=[LOGIN])
async def fabric_architecture(site: str, store: routing_intent.RoutingStore = Depends(get_routing_store),
                              netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """The fabric's architecture: symmetric, or centralized with its gateway pair; its
    NTP servers (set here once the fabric exists); and its other settings, read only."""
    try:
        from orchestrator import dhcp_relay

        from orchestrator import ipv6

        gateways, ntp, settings, netstream, syslog, relay_pool, (pool6, _) = await asyncio.gather(
            store.gateways(site), fabric_ntp.get_ntp(netbox, site), fabric_ntp.get_settings(netbox, site),
            fabric_ntp.get_netstream(netbox, site), fabric_ntp.get_syslog(netbox, site), dhcp_relay.pool(netbox, site),
            ipv6.prefixes(netbox, site))
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    architecture = {"architecture": "symmetric", "gateway_pair": None} if gateways is None else \
        {"architecture": "centralized", "gateway_pair": sorted(gateways)}
    syslog = {k: syslog[k] for k in ("enabled", "host", "port")}   # syslog (`owned` is the generator's)
    return {"site": site, **architecture, "ntp": ntp, "settings": settings, "netstream": netstream, "syslog": syslog,
            "relay_pool": relay_pool["prefix"] if relay_pool else None, "ipv6_pool": pool6["prefix"] if pool6 else None,
            "deploys_allowed": site in deploys.allowed_sites()}


@router.put("/fabrics/{site}/snmp", dependencies=INTENT_WRITE)
async def set_fabric_snmp(site: str, location: str = Body(default=""), contact: str = Body(default=""),
                          user: str = Depends(current_user), netbox: NetBoxClient = Depends(get_netbox),
                          accounts: Accounts = Depends(get_accounts)) -> dict:
    """SNMP location and contact of every switch of the fabric: intent, deployed as usual."""
    return await _segment_call(fabric_ntp.set_snmp(netbox, site, location, contact), accounts, user,
                               "fabric.snmp", f"{site} location={location!r} contact={contact!r}")


@router.put("/fabrics/{site}/gateway-mac", dependencies=INTENT_WRITE)
async def set_fabric_gateway_mac(site: str, mac: str = Body(embed=True), user: str = Depends(current_user),
                                 netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """The fabric's anycast gateway MAC: every VBDIF changes at the next deploy."""
    return {"mac": await _segment_call(fabric_ntp.set_gateway_mac(netbox, site, mac), accounts, user,
                                       "fabric.gateway-mac", f"{site} mac={mac}")}


@router.get("/fabrics/{site}/deletion", dependencies=[LOGIN, ADMIN])
async def fabric_deletion(site: str, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """What deleting the fabric would remove, and what still prevents it."""
    try:
        return await fabric_delete.plan(netbox, site)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/fabrics/{site}", dependencies=[*INTENT_WRITE, ADMIN])
async def delete_fabric(site: str, confirm: str = Body(embed=True), user: str = Depends(current_user),
                        netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts),
                        settings: PlatformSettings = Depends(get_settings)) -> dict:
    """Delete a fabric from NetBox: every switch planned, no deploy running, its name typed.
    Nothing is sent to any switch; the platform's history stays."""
    result = await _segment_call(fabric_delete.delete(netbox, site, confirm), accounts, user, "fabric.delete", site)
    if site in deploys.allowed_sites():
        await settings.set("DEPLOY_ALLOWED_SITES", ",".join(sorted(deploys.allowed_sites() - {site})), user)
    return result


class UsualWindow(BaseModel):
    days: list[str]       # mon .. sun
    start: str            # HH:MM in `zone`
    minutes: int
    zone: str             # e.g. Europe/Paris: the window follows its daylight saving


@router.get("/fabrics/{site}/usual-window", dependencies=[LOGIN])
async def get_usual_window(site: str) -> dict:
    """The fabric's usual change window and its openings of the coming week (UTC), offered when scheduling."""
    from orchestrator import windows

    usual = windows.usual_windows().get(site)
    at = db.now()
    upcoming = [{"start": start.isoformat(), "end": end.isoformat()}
                for start, end in (windows.occurrences(usual, at) if usual else []) if end > at]
    return {"usual": usual, "next": upcoming[0] if upcoming else None, "upcoming": upcoming}


@router.put("/fabrics/{site}/usual-window", dependencies=[LOGIN, APPROVE])
async def set_usual_window(site: str, usual: UsualWindow | None = Body(default=None), user: str = Depends(current_user),
                           netbox: NetBoxClient = Depends(get_netbox),
                           settings: PlatformSettings = Depends(get_settings)) -> dict:
    """Set or clear (no body) a fabric's usual change window: approvers and admins.
    A platform setting, not intent: no switch changes."""
    from orchestrator import windows

    if await netbox.find_one("dcim/sites", slug=site) is None:
        raise HTTPException(status_code=404, detail=f"no fabric {site!r}")
    every = windows.usual_windows()
    try:
        if usual is None:
            every.pop(site, None)
        else:
            every[site] = windows.check_usual(usual.model_dump())
        await settings.set("CHANGE_WINDOWS", json.dumps(every), user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await get_usual_window(site)


@router.put("/fabrics/{site}/deploys", dependencies=[LOGIN, ADMIN])
async def set_fabric_deploys(site: str, allowed: bool = Body(), confirm: str = Body(default=""),
                             user: str = Depends(current_user), netbox: NetBoxClient = Depends(get_netbox),
                             settings: PlatformSettings = Depends(get_settings)) -> dict:
    """Allow or stop deploys to one fabric. Allowing asks for the fabric's name, typed."""
    if await netbox.find_one("dcim/sites", slug=site) is None:
        raise HTTPException(status_code=404, detail=f"no fabric {site!r}")
    if allowed and confirm.strip() != site:
        raise HTTPException(status_code=400, detail=f"type the fabric's name ({site}) to allow deploys to it")
    sites = deploys.allowed_sites() | {site} if allowed else deploys.allowed_sites() - {site}
    await settings.set("DEPLOY_ALLOWED_SITES", ",".join(sorted(sites)), user)
    return {"site": site, "deploys_allowed": allowed}


class NtpSettings(BaseModel):
    servers: list[str]               # empty: the platform manages no NTP on this fabric
    vpn_instance: str | None = None  # e.g. _management_vpn_ when the servers are reached over MEth


@router.put("/fabrics/{site}/ntp", dependencies=INTENT_WRITE)
async def set_fabric_ntp(site: str, ntp: NtpSettings, user: str = Depends(current_user),
                         netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Set the fabric's NTP servers (the GUI owns them after the first seed)."""
    return await _segment_call(fabric_ntp.set_ntp(netbox, site, ntp.servers, ntp.vpn_instance), accounts, user,
                               "fabric.ntp", f"{site} servers={','.join(ntp.servers) or 'none'} vpn={ntp.vpn_instance}")


class OverrideValue(BaseModel):
    value: object = None


class OverridePath(BaseModel):
    path: str


OVERRIDE_WRITE = [*INTENT_WRITE, APPROVE]


@router.get("/fabrics/{site}/overrides", dependencies=[LOGIN])
async def fabric_overrides(site: str, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """What an imported fabric keeps the way it was found, and what it left unmanaged."""
    return await overrides.view(netbox, site)


@router.post("/fabrics/{site}/overrides/preview", dependencies=[LOGIN, APPROVE])
async def preview_override(site: str, body: OverridePath, netbox: NetBoxClient = Depends(get_netbox),
                           annet: AnnetRunner = Depends(get_annet)) -> dict:
    """The patch "Make it standard" would give; nothing is saved."""
    try:
        return {"patches": await overrides.preview(netbox, annet, site, body.path)}
    except (segs.SegmentError, AnnetError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/fabrics/{site}/overrides/{path:path}", dependencies=OVERRIDE_WRITE)
async def set_override(site: str, path: str, body: OverrideValue, user: str = Depends(current_user),
                       netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(overrides.put(netbox, site, path, body.value), accounts, user,
                               "fabric.override", f"{site} {path}")


@router.delete("/fabrics/{site}/overrides/{path:path}", dependencies=OVERRIDE_WRITE)
async def make_standard(site: str, path: str, user: str = Depends(current_user),
                        netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(overrides.remove(netbox, site, path), accounts, user,
                               "fabric.override-standard", f"{site} {path}")
