"""Routing intent and its import from the switches."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from orchestrator import segments as segs
from orchestrator import routing_import, routing_intent
from orchestrator.auth import Accounts
from orchestrator.netbox import NetBoxClient, NetBoxError
from orchestrator.deps import (
    APPROVE, current_user, get_accounts, get_importer, get_netbox, get_routing_store, INTENT_WRITE, LOGIN,
    _segment_call,
)

router = APIRouter()


@router.get("/routing", dependencies=[LOGIN])
async def get_routing_intent(site: str, store: routing_intent.RoutingStore = Depends(get_routing_store)) -> dict:
    """A fabric's routing intent per device, and what is retired (still to be removed)."""
    try:
        return (await store.load(site)).model_dump(mode="json", exclude_defaults=True)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/routing/{site}/targets", dependencies=[LOGIN])
async def routing_targets(site: str, store: routing_intent.RoutingStore = Depends(get_routing_store),
                          netbox: NetBoxClient = Depends(get_netbox)) -> list[dict]:
    """Where routing intent is set in a fabric: each unpaired device, each M-LAG pair and each
    switch of a pair on its own (`pair` names its pair), with its devices and the VPN instances
    its routing objects may use (besides the global table)."""
    try:
        state = await store.fabric_state(site)   # may adopt legacy segments (a write): first
        found, gateways, borders = await asyncio.gather(netbox.find("dcim/devices", site=site), store.gateways(site),
                                                        store.borders(site))
        devices = sorted(found, key=lambda d: d["name"])
        by_name = {d["name"]: d for d in devices}
        targets: dict[str, list[dict]] = {}
        pairs: dict[str, str] = {}
        for device in devices:
            # From the list itself: a lookup per device made this page take seconds (timing log).
            peer = await store.peer_of(device)
            target = "+".join(sorted((device["name"], peer))) if peer else device["name"]
            member = {"name": device["name"], "role": (device.get("role") or {}).get("slug"),
                      "status": routing_intent.status_of(device)}
            targets.setdefault(target, []).append(member)
            if peer:
                targets[device["name"]] = [member]
                pairs[device["name"]] = target
        return [{"target": target, "devices": members, "pair": pairs.get(target),
                 "vpns": await store.vpns(site, target, state, by_name, gateways, borders)}
                for target, members in sorted(targets.items())]
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.put("/routing/{site}/devices/{device}", dependencies=INTENT_WRITE)
async def set_device_routing(
    site: str, device: str, routing: routing_intent.DeviceRouting,
    user: str = Depends(current_user), store: routing_intent.RoutingStore = Depends(get_routing_store),
    accounts: Accounts = Depends(get_accounts),
) -> dict:
    """Replace one device's routing intent; what it no longer has is retired (removed by the
    next deploy). The body is validated: only forms verified on the lab switch are accepted."""
    document = await _segment_call(store.set_device(site, device, routing), accounts, user, "routing.set",
                                   f"{site} {device}")
    return document.model_dump(mode="json", exclude_defaults=True)


@router.put("/routing/{site}/targets/{target}", dependencies=INTENT_WRITE)
async def save_target_routing(
    site: str, target: str, body: routing_intent.TargetSave,
    user: str = Depends(current_user), store: routing_intent.RoutingStore = Depends(get_routing_store),
    accounts: Accounts = Depends(get_accounts),
) -> dict:
    """Replace a target's routing intent and change the fabric's library (fabric-wide prefix
    lists and route-policies, any target may use them) in one write."""
    shared = body.library.model_dump(mode="json", exclude_defaults=True) if body.library else {}
    detail = f"{site} {target}" + "".join(f"; fabric-wide {kind.replace('_', ' ')[:-1]} {n} {'deleted' if v is None else 'set'}"
                                          for kind, named in shared.items() for n, v in named.items())
    document = await _segment_call(store.set_device(site, target, body.routing, body.library), accounts, user,
                                   "routing.set", detail)
    return document.model_dump(mode="json", exclude_defaults=True)


@router.post("/routing/{site}/library/promote", dependencies=[*INTENT_WRITE, APPROVE])
async def promote_routing_library(site: str, apply: bool = False, user: str = Depends(current_user),
                                  store: routing_intent.RoutingStore = Depends(get_routing_store),
                                  accounts: Accounts = Depends(get_accounts)) -> dict:
    """Move the lists and policies every target has identically, and uses, into the fabric's
    library; nothing changes on a switch. `apply` false: preview."""
    async def run():
        document, moved, kept = routing_intent.promoted(await store.load(site))
        if apply and moved:
            await store.save(site, document)
        return {"moved": moved, "kept": kept, "applied": apply and bool(moved)}

    if not apply:
        try:
            return await run()
        except segs.SegmentError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except NetBoxError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
    result = await _segment_call(run(), accounts, user, "routing.library", f"{site}: moved into the fabric library")
    return result


@router.get("/routing/{site}/library", dependencies=[LOGIN])
async def routing_library(site: str, store: routing_intent.RoutingStore = Depends(get_routing_store)) -> dict:
    """The fabric's library and, for each object, the targets whose routing uses it."""
    try:
        document = await store.load(site)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {**document.library.model_dump(mode="json", include=set(routing_intent.SHARED)),
            "used_by": routing_intent.used_by(document)}


@router.get("/routing/{site}/imports", dependencies=[LOGIN])
async def routing_imports(site: str, importer: routing_import.RoutingImporter = Depends(get_importer),
                          netbox: NetBoxClient = Depends(get_netbox)) -> list[dict]:
    """The latest auto-import per device of the fabric: what was adopted, what is unmanaged."""
    try:
        names = {d["name"] for d in await netbox.find("dcim/devices", site=site)}
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return [vars(r) for name, r in sorted(importer.reports.items()) if name in names]


@router.post("/routing/{site}/devices/{device}/import", dependencies=INTENT_WRITE)
async def import_routing(site: str, device: str, user: str = Depends(current_user),
                         importer: routing_import.RoutingImporter = Depends(get_importer),
                         netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """Read the switch now and adopt its hand-made routing objects (also runs after each drift
    run and before each deploy). Read-only on the switch."""
    try:
        found = await netbox.find_one("dcim/devices", name=device)
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if found is None or (found.get("site") or {}).get("slug") != site:
        raise HTTPException(status_code=404, detail=f"{device} is not a device of fabric {site}")
    return vars(await importer.run(device, user=user))
