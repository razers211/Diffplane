"""Host keys, ZTP, reading a switch's inventory, racks, location and role."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from orchestrator import hostkeys, segments as segs
from orchestrator import routes as rv
from orchestrator.access import Access
from orchestrator import ztp as zt, locations as loc
from orchestrator.auth import audit
from orchestrator.auth import Accounts
from orchestrator.netbox import NetBoxClient, NetBoxError
from orchestrator.prometheus import PrometheusClient, PrometheusError
from orchestrator import switch_inventory
from orchestrator.deps import (
    APPROVE, current_user, get_access, get_accounts, get_locations, get_netbox, get_prometheus, get_ztp,
    _intent_unfrozen, INTENT_WRITE, LOGIN, _netbox, OPERATE, _segment_call, _slug, _visible_devices,
)

router = APIRouter()


# -- host keys from the GUI -----------------------------------------------------------------

async def _hostkey_call(call, accounts: Accounts | None = None, user: str | None = None, action: str = "",
                        detail=None):
    try:
        result = await call
    except hostkeys.HostKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if accounts is not None:
        await audit(accounts.sessions, user, action, detail=detail(result) if callable(detail) else detail)
    return result


@router.post("/devices/{name}/hostkeys/fetch", dependencies=[LOGIN, OPERATE])
async def fetch_hostkeys(name: str, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """The host keys the switch offers now (ports 22 and 830), with fingerprints, next to the pins.
    Fetching proves nothing: a person compares the fingerprints with the switch's own display."""
    return await _hostkey_call(hostkeys.offer(netbox, name))


class ApprovedKeys(BaseModel):
    keys: list[str]   # exactly the keys shown by the fetch, "<type> <base64>"


@router.put("/devices/{name}/hostkeys", dependencies=[LOGIN, APPROVE])
async def pin_hostkeys(name: str, body: ApprovedKeys, user: str = Depends(current_user),
                       netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Pin the approved keys (approver), if the switch still offers exactly these; then read what the
    switch is (model, software, ports), which may not stop the pin."""
    result = await _hostkey_call(hostkeys.pin_approved(netbox, name, body.keys), accounts, user, "hostkey.pin",
                                 lambda r: f"{name} {r['address']} " + " ".join(r["fingerprints"]))
    return {**result, "inventory": await _learn_quietly(netbox, accounts, user, name)}


@router.delete("/devices/{name}/hostkeys", dependencies=[LOGIN, APPROVE])
async def forget_hostkeys(name: str, user: str = Depends(current_user), netbox: NetBoxClient = Depends(get_netbox),
                          accounts: Accounts = Depends(get_accounts)) -> dict:
    """Forget the pins: Annet and the collector refuse the switch until it is pinned again."""
    return await _hostkey_call(hostkeys.forget_pins(netbox, name), accounts, user, "hostkey.forget", name)


class ZtpRegistration(BaseModel):
    mac: str
    address: str                    # management address with its mask
    gateway: str
    interface: str = "MEth0/0/0"


@router.get("/ztp", dependencies=[LOGIN])
async def list_ztp(ztp: zt.Ztp = Depends(get_ztp), access: Access = Depends(get_access),
                   netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """Registered switches and what they reported; the keys are public, nothing here is secret."""
    try:
        registrations = await ztp.registrations()
        if access.limited:
            mine = await _visible_devices(netbox, access)
            registrations = {name: reg for name, reg in registrations.items() if name in mine}
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    for reg in registrations.values():   # its provisioning window, and fingerprints to compare on the console
        reg["expires_at"] = int(reg.get("registered_at", 0) + zt.window_seconds())
        reg["expired"] = zt.expired(reg)
        if reg.get("offered"):
            reg["offered_keys"] = {port: [hostkeys.key_info(k) for k in keys] for port, keys in reg["offered"].items()}
    return {"registrations": registrations, "configured": bool(zt.dnsmasq_conf())}


@router.put("/devices/{name}/ztp", dependencies=INTENT_WRITE)
async def register_ztp(name: str, body: ZtpRegistration, user: str = Depends(current_user),
                       ztp: zt.Ztp = Depends(get_ztp), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Register a planned switch for zero-touch provisioning."""
    return await _segment_call(ztp.register(name, body.mac, body.address, body.gateway, body.interface), accounts,
                               user, "ztp.register", f"{name} {body.mac} {body.address} gw {body.gateway}")


@router.delete("/devices/{name}/ztp", dependencies=INTENT_WRITE)
async def unregister_ztp(name: str, user: str = Depends(current_user), ztp: zt.Ztp = Depends(get_ztp),
                         accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(ztp.unregister(name), accounts, user, "ztp.unregister", name)


@router.get("/devices/{name}/ztp/files", dependencies=[LOGIN, OPERATE])
async def ztp_files(name: str, ztp: zt.Ztp = Depends(get_ztp)) -> dict:
    """The intermediate file and the first-boot config the switch gets, passwords masked."""
    try:
        return await ztp.preview(name)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


async def _ztp_approval(name: str, request: Request, access: Access = Depends(get_access)) -> None:
    """Approvers, or an operator an admin lets approve the switch's fabric."""
    if access.may_approve(None):
        return
    site = None
    if access.limited:
        device = await (await _netbox(request)).find_one("dcim/devices", name=name)
        site = _slug((device or {}).get("site"))
    if not access.may_approve(site):
        raise HTTPException(status_code=403, detail="approving a provisioned switch needs the approver role"
                                                    + (f" or approval rights on {site}" if site else "")
                                                    + f" (you are {access.role})")


@router.post("/devices/{name}/ztp/approve", dependencies=[LOGIN, OPERATE, Depends(_ztp_approval), Depends(_intent_unfrozen)])
async def approve_ztp(name: str, body: ApprovedKeys, user: str = Depends(current_user), ztp: zt.Ztp = Depends(get_ztp),
                      accounts: Accounts = Depends(get_accounts), netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """Approve a provisioned switch's reported keys: pinned, then the switch is activated and read."""
    result = await _segment_call(ztp.approve(name, body.keys), accounts, user, "ztp.approve", name)
    return {**result, "inventory": await _learn_quietly(netbox, accounts, user, name)}


# -- what a switch is, read from it ------------------------------------------------------------

async def _learn(netbox: NetBoxClient, accounts: Accounts, user: str | None, name: str) -> dict:
    result = await switch_inventory.learn(netbox, name, await switch_inventory.read(rv.RouteReader(netbox), name))
    if result["changed"] or result["blocked"]:
        await audit(accounts.sessions, user, "switch.inventory", detail=switch_inventory.summary(result))
    return result


async def _learn_quietly(netbox: NetBoxClient, accounts: Accounts, user: str | None, name: str) -> dict:
    """After a pin: what was read, or why not; never an error for the call that pinned."""
    try:
        return await _learn(netbox, accounts, user, name)
    except Exception as exc:   # a switch that cannot be read yet: the hourly sweep or the button retries
        logging.getLogger(__name__).warning("reading %s after its pin failed: %s", name, exc)
        return {"error": str(exc)}


@router.post("/devices/{name}/inventory", dependencies=[LOGIN, OPERATE])
async def read_switch_inventory(name: str, user: str = Depends(current_user), netbox: NetBoxClient = Depends(get_netbox),
                                accounts: Accounts = Depends(get_accounts)) -> dict:
    """Read the switch's model, software and ports now and bring NetBox in step."""
    try:
        return await _learn(netbox, accounts, user, name)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/devices/{name}/inventory", dependencies=[LOGIN])
async def switch_inventory_state(name: str, netbox: NetBoxClient = Depends(get_netbox),
                                 prometheus: PrometheusClient | None = Depends(get_prometheus)) -> dict:
    """The model NetBox has, and the model, release and patch the switch last reported (collector)."""
    device = await netbox.find_one("dcim/devices", name=name)
    if device is None:
        raise HTTPException(status_code=404, detail=f"no device {name!r}")
    reported = None
    try:   # the name is a NetBox device's (validated when it was made), so safe in the query
        rows = await prometheus.query(f'fabric_device_info{{device="{name}"}}') if prometheus else []
        if rows:
            labels = rows[0][0]
            reported = {k: labels.get(k, "") for k in ("model", "version", "patch")}
    except PrometheusError:
        pass
    return {"device": name, "model": (device.get("device_type") or {}).get("model"), "serial": device.get("serial") or "",
            "reported": reported}


@router.get("/fabrics/{site}/racks", dependencies=[LOGIN])
async def list_racks(site: str, locations: loc.Locations = Depends(get_locations)) -> dict:
    """The fabric's locations (rooms, halls) and racks, with the devices in each rack."""
    try:
        return await locations.racks(site)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


class NewRack(BaseModel):
    location: str
    rack: str
    u_height: int = 42


# Location is not device config: no intent freeze, but an operator's change, audited.
@router.post("/fabrics/{site}/racks", status_code=201, dependencies=[LOGIN, OPERATE])
async def add_rack(site: str, rack: NewRack, user: str = Depends(current_user),
                   locations: loc.Locations = Depends(get_locations), accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(locations.add_rack(site, rack.location, rack.rack, rack.u_height), accounts, user,
                               "rack.create", f"{site} {rack.location}/{rack.rack} {rack.u_height}U")


@router.delete("/fabrics/{site}/racks", dependencies=[LOGIN, OPERATE])
async def delete_rack(site: str, location: str, rack: str, user: str = Depends(current_user),
                      locations: loc.Locations = Depends(get_locations), accounts: Accounts = Depends(get_accounts)) -> dict:
    await _segment_call(locations.delete_rack(site, location, rack), accounts, user, "rack.delete",
                        f"{site} {location}/{rack}")
    return {}


class Placement(BaseModel):
    location: str | None = None   # none: take the device out of its rack
    rack: str | None = None
    position: int | None = None   # lowest U the device takes
    face: str | None = None       # front or rear, with a position


@router.put("/devices/{name}/location", dependencies=[LOGIN, OPERATE])
async def place_device(name: str, place: Placement, user: str = Depends(current_user),
                       locations: loc.Locations = Depends(get_locations), accounts: Accounts = Depends(get_accounts)) -> dict:
    where = f"{place.location}/{place.rack}" + (f" U{place.position} {place.face}" if place.position else "") \
        if place.rack else "none"
    return await _segment_call(locations.place(name, place.location, place.rack, place.position, place.face),
                               accounts, user, "device.location", f"{name} {where}")


class RoleChange(BaseModel):
    role: str


@router.put("/devices/{name}/role", dependencies=INTENT_WRITE)
async def set_device_role(name: str, change: RoleChange, user: str = Depends(current_user),
                          netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Leaf <-> border-leaf, the M-LAG peer with it."""
    from orchestrator.switch_role import change_role

    return await _segment_call(change_role(netbox, name, change.role), accounts, user, "device.role", f"{name} {change.role}")
