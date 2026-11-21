"""Microsegmentation, QoS, port settings and port protection."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orchestrator.annet import AnnetRunner
from orchestrator import segments as segs
from orchestrator import microseg, port_settings, protection, qos
from orchestrator.auth import Accounts
from orchestrator.netbox import NetBoxClient, NetBoxError
from orchestrator.prometheus import PrometheusClient, PrometheusError
from orchestrator.deps import (
    current_user, get_accounts, get_annet, get_netbox, get_prometheus, INTENT_WRITE, LOGIN,
    _microseg_reader, OPERATE, _segment_call,
)

router = APIRouter()


# -- microsegmentation: Huawei EPGs and GBP contracts ---------------------

class MicrosegMode(BaseModel):
    mode: str   # off, learn (permit between EPGs, counters on) or enforce (deny between EPGs)


class MicrosegGroup(BaseModel):
    segment: int                  # the routed segment's L2 VNI
    name: str
    prefixes: list[str] = []      # inside the segment's subnets
    macs: list[str] = []          # hosts bridged in the segment
    inside: str | None = None     # deny; permit is the default


class MicrosegInside(BaseModel):
    inside: str


class MicrosegContract(BaseModel):
    source: int = Field(alias="from")
    destination: int = Field(alias="to")
    services: list[dict]          # [{"protocol": "tcp", "ports": [5432, 5433]}], or [{"protocol": "any"}]


@router.get("/fabrics/{site}/microsegmentation", dependencies=[LOGIN])
async def get_microsegmentation(site: str, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """Mode, EPGs (every routed segment's, named groups inside segments), contracts and the most
    a switch would hold of each resource."""
    try:
        return await microseg.get(netbox, site)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/devices/{name}/microsegmentation", dependencies=[LOGIN])
async def device_microsegmentation(name: str, reader=Depends(_microseg_reader)) -> dict:
    """Read now from the switch: TCAM use per chip, each contract's counters (classifier
    `GBP-<from>-<to>`), and rules or members it could not install. Read only, nothing stored."""
    try:
        return await microseg.state(reader, name)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.put("/fabrics/{site}/microsegmentation/mode", dependencies=INTENT_WRITE)
async def set_microsegmentation_mode(site: str, body: MicrosegMode, user: str = Depends(current_user),
                                     netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts),
                                     reader=Depends(_microseg_reader)) -> dict:
    """Off, Learn or Enforce. Turning it on is refused while an active switch that would get it
    does not announce the huawei-microsegmentation module (V300R025 or later)."""
    async def change():
        if body.mode != "off":
            await microseg.check_software(netbox, site, lambda name: reader.has_module(name, microseg.MODULE))
        return await microseg.set_mode(netbox, site, body.mode)
    return await _segment_call(change(), accounts, user, "microseg.mode", f"{site} {body.mode}")


@router.post("/fabrics/{site}/microsegmentation/groups", dependencies=INTENT_WRITE)
async def create_microseg_group(site: str, body: MicrosegGroup, user: str = Depends(current_user),
                                netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(microseg.set_group(netbox, site, body.segment, body.name, body.prefixes, body.macs, body.inside),
                               accounts, user, "microseg.group", f"{site} new {body.name} in {body.segment}")


@router.put("/fabrics/{site}/microsegmentation/groups/{epg}", dependencies=INTENT_WRITE)
async def update_microseg_group(site: str, epg: int, body: MicrosegGroup, user: str = Depends(current_user),
                                netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(microseg.set_group(netbox, site, body.segment, body.name, body.prefixes, body.macs, body.inside,
                                                  epg_id=epg), accounts, user, "microseg.group", f"{site} EPG {epg} {body.name}")


@router.delete("/fabrics/{site}/microsegmentation/groups/{epg}", dependencies=INTENT_WRITE)
async def delete_microseg_group(site: str, epg: int, user: str = Depends(current_user),
                                netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    await _segment_call(microseg.delete_group(netbox, site, epg), accounts, user, "microseg.group", f"{site} delete EPG {epg}")
    return {"deleted": epg}


@router.put("/fabrics/{site}/microsegmentation/epgs/{epg}/inside", dependencies=INTENT_WRITE)
async def set_microseg_inside(site: str, epg: int, body: MicrosegInside, user: str = Depends(current_user),
                              netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(microseg.set_inside(netbox, site, epg, body.inside), accounts, user,
                               "microseg.inside", f"{site} EPG {epg} {body.inside}")


@router.put("/fabrics/{site}/microsegmentation/contracts", dependencies=INTENT_WRITE)
async def set_microseg_contract(site: str, body: MicrosegContract, user: str = Depends(current_user),
                                netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Allow one EPG to another for these services (replaces the contract between them); the
    reply rules are generated."""
    return await _segment_call(microseg.set_contract(netbox, site, body.source, body.destination, body.services), accounts, user,
                               "microseg.contract", f"{site} {body.source} -> {body.destination} {body.services}")


@router.delete("/fabrics/{site}/microsegmentation/contracts/{source}/{destination}", dependencies=INTENT_WRITE)
async def delete_microseg_contract(site: str, source: int, destination: int, user: str = Depends(current_user),
                                   netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    await _segment_call(microseg.delete_contract(netbox, site, source, destination), accounts, user,
                        "microseg.contract", f"{site} delete {source} -> {destination}")
    return {"deleted": [source, destination]}


# -- QoS: one queue plan on every port, DSCP marks per segment and port ------

class QosEnabled(BaseModel):
    enabled: bool


class QosMark(BaseModel):
    mark: str   # storage (AF31) or scavenger (CS1)


class QosPortMark(BaseModel):
    device: str
    port: str                 # a physical port or an Eth-Trunk
    mark: str | None = None   # None: no mark


@router.get("/fabrics/{site}/qos", dependencies=[LOGIN])
async def get_qos(site: str, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """On or off, the segment and port marks, and the queue plan every port gets while on."""
    from generators import qos as plan

    try:
        doc = await qos.get(netbox, site)
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {**doc, "plan": {"strict": [5, 6, 7], "weights": plan.WEIGHTS, "marks": plan.MARKS}}


@router.put("/fabrics/{site}/qos/enabled", dependencies=INTENT_WRITE)
async def set_qos_enabled(site: str, body: QosEnabled, user: str = Depends(current_user),
                          netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(qos.set_enabled(netbox, site, body.enabled), accounts, user, "qos.enabled",
                               f"{site} {'on' if body.enabled else 'off'}")


@router.put("/fabrics/{site}/qos/segments/{vni}", dependencies=INTENT_WRITE)
async def set_qos_segment(site: str, vni: int, body: QosMark, user: str = Depends(current_user),
                          netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(qos.set_segment(netbox, site, vni, body.mark), accounts, user, "qos.mark",
                               f"{site} segment {vni} {body.mark}")


@router.delete("/fabrics/{site}/qos/segments/{vni}", dependencies=INTENT_WRITE)
async def delete_qos_segment(site: str, vni: int, user: str = Depends(current_user),
                             netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(qos.set_segment(netbox, site, vni, None), accounts, user, "qos.mark",
                               f"{site} segment {vni} no mark")


@router.put("/fabrics/{site}/qos/ports", dependencies=INTENT_WRITE)
async def set_qos_port(site: str, body: QosPortMark, user: str = Depends(current_user),
                       netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Mark everything entering a port or Eth-Trunk (an M-LAG trunk on both leaves); mark null removes it."""
    return await _segment_call(qos.set_port(netbox, site, body.device, body.port, body.mark), accounts, user, "qos.mark",
                               f"{site} {body.device} {body.port} {body.mark or 'no mark'}")


# -- port settings: name, on/off and frame size of server ports ----------

class PortSettingsEnabled(BaseModel):
    enabled: bool


class PortSettings(BaseModel):
    description: str | None = None   # the port's name; "" removes it
    enabled: bool | None = None      # False: shut down
    frame: int | None = None         # jumbo frame size (Expert); null: the switch's default 9216
    speed: int | None = None         # copper ports only: fixed Mbit/s (10, 100, 1000, 10000); null: auto


@router.get("/fabrics/{site}/port-settings", dependencies=[LOGIN])
async def get_port_settings(site: str, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    try:
        return await port_settings.document(netbox, site)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/fabrics/{site}/port-settings/adoption", dependencies=[LOGIN, OPERATE])
async def port_settings_adoption(site: str, netbox: NetBoxClient = Depends(get_netbox),
                                 annet: AnnetRunner = Depends(get_annet)) -> dict:
    """What turning port settings on would adopt from the switches (read now; nothing written)."""
    try:
        found = await port_settings.adoption(netbox, annet, site)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {**found, "changes": [{k: v for k, v in c.items() if k != "id"} for c in found["changes"]]}


@router.put("/fabrics/{site}/port-settings/enabled", dependencies=INTENT_WRITE)
async def set_port_settings_enabled(site: str, body: PortSettingsEnabled, user: str = Depends(current_user),
                                    netbox: NetBoxClient = Depends(get_netbox), annet: AnnetRunner = Depends(get_annet),
                                    accounts: Accounts = Depends(get_accounts)) -> dict:
    """On: adopt what the switches have, then manage; off: stop managing (nothing removed)."""
    return await _segment_call(port_settings.set_enabled(netbox, annet, site, body.enabled), accounts, user,
                               "ports.enabled", f"{site} {'on' if body.enabled else 'off'}")


@router.get("/devices/{name}/port-settings/{port:path}", dependencies=[LOGIN])
async def get_port_setting(name: str, port: str, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    try:
        return await port_settings.get_port(netbox, name, port)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.put("/devices/{name}/port-settings/{port:path}", dependencies=INTENT_WRITE)
async def set_port_setting(name: str, port: str, body: PortSettings, user: str = Depends(current_user),
                           netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Name, on/off, frame size and (copper) speed of a server port or trunk (an M-LAG trunk on both switches)."""
    given = {k: getattr(body, k) for k in body.model_fields_set}
    detail = " ".join(f"{k}={v!r}" for k, v in given.items())
    return await _segment_call(port_settings.set_port(netbox, name, port, **given), accounts, user, "ports.set",
                               f"{name} {port} {detail}")


# -- port protection: storm control, BPDU guard, loop detection ---------------

class ProtectionMode(BaseModel):
    mode: str   # off, watch (log and trap only) or on


class ProtectionTemplate(BaseModel):
    template: str | None = None   # server, hypervisor, or null: unprotected


@router.get("/fabrics/{site}/protection", dependencies=[LOGIN])
async def get_protection(site: str, netbox: NetBoxClient = Depends(get_netbox),
                         prometheus: PrometheusClient | None = Depends(get_prometheus)) -> dict:
    """Off, Watch or On, the protected ports, what each template does, and the ports protection acts
    on now (`tripped`, from the collector's last control poll; empty without Prometheus)."""
    from generators import protection as plan

    try:
        doc, devices = await asyncio.gather(protection.document(netbox, site),
                                            netbox.find("dcim/devices", site=site, status="active"))
        trips = await protection.tripped(prometheus, [d["name"] for d in devices])
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except PrometheusError:
        trips = {}
    return {"mode": doc["mode"], "ports": doc["ports"],
            "plan": {"storm": plan.STORM, "templates": plan.TEMPLATES, "recovery": plan.RECOVERY},
            "tripped": [{"device": d, "port": p, **t, "text": protection.trip_text(p, t)}
                        for (d, p), ts in sorted(trips.items()) for t in ts]}


@router.put("/fabrics/{site}/protection/mode", dependencies=INTENT_WRITE)
async def set_protection_mode(site: str, body: ProtectionMode, user: str = Depends(current_user),
                              netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(protection.set_mode(netbox, site, body.mode), accounts, user, "protection.mode",
                               f"{site} {body.mode}")


@router.get("/devices/{name}/protection/{port:path}", dependencies=[LOGIN])
async def get_port_protection(name: str, port: str, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    try:
        return await protection.get_port(netbox, name, port)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.put("/devices/{name}/protection/{port:path}", dependencies=INTENT_WRITE)
async def set_port_protection(name: str, port: str, body: ProtectionTemplate, user: str = Depends(current_user),
                              netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Protect a server port or trunk with a template (an M-LAG trunk on both switches); null removes it."""
    return await _segment_call(protection.set_port(netbox, name, port, body.template), accounts, user,
                               "protection.port", f"{name} {port} {body.template or 'none'}")
