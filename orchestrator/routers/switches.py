"""Switch management addresses, adding switches, new fabrics, inventory, removal."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator import segments as segs
from orchestrator import fabric_build, fabric_new, imports as imp, inventory as inv, lifecycle as life
from orchestrator.auth import Accounts
from orchestrator.netbox import NetBoxClient, NetBoxError
from seed import ports as seed_ports
from orchestrator.deps import (
    current_user, get_accounts, get_imports, get_lifecycle, get_netbox, INTENT_WRITE, LOGIN, OPERATE, _segment_call,
    _slug,
)

router = APIRouter()


class Activation(BaseModel):
    address: str                    # with its mask, e.g. 172.24.0.104/24
    interface: str = "MEth0/0/0"


# Activation and decommission change what deploys, drift and the collector reach: intent writes.
@router.put("/devices/{name}/management", dependencies=INTENT_WRITE)
async def activate_device(name: str, body: Activation, user: str = Depends(current_user),
                          lifecycle: life.Lifecycle = Depends(get_lifecycle),
                          accounts: Accounts = Depends(get_accounts)) -> dict:
    """Make a device active with its management address (or change that address)."""
    return await _segment_call(lifecycle.activate(name, body.address, body.interface), accounts, user,
                               "device.activate", f"{name} {body.interface} {body.address}")


@router.delete("/devices/{name}/management", dependencies=INTENT_WRITE)
async def decommission_device(name: str, user: str = Depends(current_user),
                              lifecycle: life.Lifecycle = Depends(get_lifecycle),
                              accounts: Accounts = Depends(get_accounts)) -> dict:
    """Back to planned: management address freed, host keys forgotten; the switch is untouched."""
    return await _segment_call(lifecycle.decommission(name), accounts, user, "device.decommission", name)


@router.get("/fabrics/{site}/build", dependencies=[LOGIN])
async def build_options(site: str, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """Known switch models and the fabric's spines and leaves with free ports, for adding switches."""
    try:
        return await fabric_build.FabricBuilder(netbox, site).options()
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


class NewSpine(BaseModel):
    name: str
    model: str
    links: list[dict[str, str]] = []   # [{spine_port, leaf, leaf_port}]


class NewLeafPair(BaseModel):
    leaves: list[str]
    model: str
    role: str = "leaf"
    peer_link_trunk: int = 0
    peer_link_members: list[str]
    uplinks: list[dict[str, str]] = []  # [{leaf, leaf_port, spine, spine_port}]


def _link_text(links: list[dict[str, str]]) -> str:
    return ", ".join("-".join(f"{l.get(k, '')}" for k in sorted(l)) for l in links)


class NewSwitch(BaseModel):
    name: str
    model: str
    role: str = "spine"
    management: str | None = None   # address with mask: added active; none: planned
    inventory: str | None = None    # serial of the inventory switch it is


class NewPair(BaseModel):
    leaves: list[str]
    model: str
    role: str = "leaf"
    peer_link_trunk: int = 0
    peer_link_members: list[str]
    management: dict[str, str] = {}
    inventory: dict[str, str] = {}   # leaf -> serial of the inventory switch it is
    gateway: bool = False            # a border-leaf pair as a centralized fabric's gateway pair


class NewSpinePair(BaseModel):
    spines: list[str]                # new names, or one existing single spine and a new one
    model: str                       # of the new spine(s)
    peer_link_trunk: int = 0
    peer_link_members: list[str]
    management: dict[str, str] = {}
    inventory: dict[str, str] = {}
    gateway: bool = False            # becomes the fabric's gateway pair (centralized fabrics)


class NewLink(BaseModel):
    spine: str
    spine_port: str
    leaf: str
    leaf_port: str


class NewSwitches(BaseModel):
    spines: list[NewSwitch] = []
    pairs: list[NewPair] = []
    leaves: list[NewSwitch] = []    # lone leaves (no M-LAG peer)
    spine_pairs: list[NewSpinePair] = []
    links: list[NewLink] = []
    detect: str | None = None    # the session whose switches were detected (POST /imports/{id}/detect)


@router.post("/fabrics/{site}/switches", status_code=201, dependencies=INTENT_WRITE)
async def add_switches(site: str, body: NewSwitches, user: str = Depends(current_user),
                       netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts),
                       imports: imp.Imports = Depends(get_imports)) -> dict:
    """Add several switches at once: leaf pairs, lone leaves, spines and (centralized
    fabrics) spine pairs, with their links."""
    batch, text = _batch(body)
    detected = _detected(imports, body.detect)
    result = await _segment_call(fabric_build.FabricBuilder(netbox, site).add(batch, detected), accounts, user,
                                 "fabric.add-switches", f"{site} {text}")
    _done(imports, body.detect)
    return result


def _detected(imports: imp.Imports | None, session: str | None) -> dict | None:
    if session is None:
        return None
    if imports is None:
        raise HTTPException(status_code=400, detail="no detect sessions here")
    try:
        return imports.detected(session)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _done(imports: imp.Imports | None, session: str | None) -> None:
    """The detect session ends once its switches are written (their keys are pinned)."""
    if session is not None and session in imports.sessions:
        imports.drop(session)


def _batch(body: NewSwitches) -> tuple[dict, str]:
    """The builder's batch, and its audit text."""
    batch = body.model_dump(exclude={"detect"})
    for leaf in batch["leaves"]:
        if leaf["role"] == "spine":
            leaf["role"] = "leaf"
    names = [s["name"] for s in batch["spines"]] \
        + [f"{'+'.join(p['leaves'])}{' (gateway pair)' if p['gateway'] else ''}" for p in batch["pairs"]] \
        + [leaf["name"] for leaf in batch["leaves"]] \
        + [f"{'+'.join(p['spines'])}{' (gateway pair)' if p['gateway'] else ''}" for p in batch["spine_pairs"]]
    links = ", ".join(f"{l['spine']} {l['spine_port']}-{l['leaf']} {l['leaf_port']}" for l in batch["links"])
    return batch, f"{', '.join(names)} links: {links}"


class AddressPlanRequest(BaseModel):
    supernet: str
    vendor: str = "huawei"   # the models offered are this vendor's (a fabric is one vendor)


@router.post("/fabrics/address-plan", dependencies=[LOGIN, OPERATE])
async def fabric_address_plan(body: AddressPlanRequest, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """For the new-fabric wizard: a proposed split of one private subnet into the pools,
    an unused gateway MAC, and the switch models with a known port layout."""
    try:
        prefixes = fabric_new.address_plan(body.supernet)
        mac = await fabric_new.proposed_mac(netbox)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"prefixes": prefixes, "gateway_mac": mac, "bypass_vlan": 4093,
            "models": await _models(netbox, body.vendor)}


class NewFabricSettings(BaseModel):
    name: str
    asn: int
    architecture: str = "symmetric"
    vendor: str = "huawei"   # Annet's names: huawei or arista
    prefixes: dict[str, str]
    bypass_vlan: int = 4093
    gateway_mac: str
    ntp_servers: list[str] = []
    ntp_vpn_instance: str | None = None
    snmp_location: str | None = None
    snmp_contact: str | None = None


class NewFabric(BaseModel):
    settings: NewFabricSettings
    switches: NewSwitches


@router.post("/fabrics", status_code=201, dependencies=INTENT_WRITE)
async def create_fabric(body: NewFabric, user: str = Depends(current_user), netbox: NetBoxClient = Depends(get_netbox),
                        accounts: Accounts = Depends(get_accounts), imports: imp.Imports = Depends(get_imports)) -> dict:
    """A new fabric with its first switches: everything checked before the first write."""
    batch, text = _batch(body.switches)
    detected = _detected(imports, body.switches.detect)
    s = body.settings
    result = await _segment_call(fabric_new.create(netbox, s.model_dump(), batch, detected=detected), accounts, user,
                                 "fabric.create",
                                 f"{s.name} asn={s.asn} {s.architecture} {','.join(f'{k}={v}' for k, v in s.prefixes.items())} "
                                 f"{text}")
    _done(imports, body.switches.detect)
    return result


class InventorySwitch(BaseModel):
    serial: str
    model: str
    mac: str | None = None


@router.get("/inventory", dependencies=[LOGIN])
async def list_inventory(netbox: NetBoxClient = Depends(get_netbox)) -> list[dict]:
    """Switches not yet in a fabric."""
    try:
        return await inv.Inventory(netbox).list()
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/inventory/models", dependencies=[LOGIN])
async def inventory_models(netbox: NetBoxClient = Depends(get_netbox)) -> list[str]:
    """Switch models with a known port layout: the only ones the platform can place in a fabric."""
    return sorted(await _models(netbox))


async def _models(netbox: NetBoxClient, vendor: str | None = None) -> dict[str, list[str]]:
    """Every model with a known port layout (learned from switches, or known from the start);
    `vendor`: that vendor's only."""
    await seed_ports.refresh(netbox)
    return {m: seed_ports.ports_of(m) for m in seed_ports.known_models(vendor)}


@router.post("/inventory", status_code=201, dependencies=[LOGIN, OPERATE])
async def add_inventory(body: InventorySwitch, user: str = Depends(current_user), netbox: NetBoxClient = Depends(get_netbox),
                        accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(inv.Inventory(netbox).add(body.serial, body.model, body.mac), accounts, user,
                               "inventory.add", f"{body.serial} {body.model} mac={body.mac}")


@router.put("/inventory/{serial}", dependencies=[LOGIN, OPERATE])
async def update_inventory(serial: str, body: InventorySwitch, user: str = Depends(current_user),
                           netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(inv.Inventory(netbox).update(serial, body.model, body.mac), accounts, user,
                               "inventory.update", f"{serial} {body.model} mac={body.mac}")


@router.delete("/inventory/{serial}", dependencies=[LOGIN, OPERATE])
async def remove_inventory(serial: str, user: str = Depends(current_user), netbox: NetBoxClient = Depends(get_netbox),
                           accounts: Accounts = Depends(get_accounts)) -> dict:
    await _segment_call(inv.Inventory(netbox).remove(serial), accounts, user, "inventory.remove", serial)
    return {"removed": serial}


@router.post("/fabrics/{site}/spines", status_code=201, dependencies=INTENT_WRITE)
async def add_spine(site: str, body: NewSpine, user: str = Depends(current_user),
                    netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Add a spine (planned) with its links to existing leaves."""
    changes = await _segment_call(fabric_build.FabricBuilder(netbox, site).add_spine(body.name, body.model, body.links),
                                  accounts, user, "fabric.add-spine",
                                  f"{site} {body.name} {body.model} links: {_link_text(body.links)}")
    return {"devices": [body.name], "changes": changes}


@router.post("/fabrics/{site}/leaf-pairs", status_code=201, dependencies=INTENT_WRITE)
async def add_leaf_pair(site: str, body: NewLeafPair, user: str = Depends(current_user),
                        netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Add an M-LAG leaf pair (planned) with its peer-link and uplinks to existing spines."""
    changes = await _segment_call(
        fabric_build.FabricBuilder(netbox, site).add_leaf_pair(body.leaves, body.model, body.role, body.peer_link_trunk,
                                                               body.peer_link_members, body.uplinks),
        accounts, user, "fabric.add-leaf-pair",
        f"{site} {'+'.join(body.leaves)} {body.model} {body.role} peer-link Eth-Trunk{body.peer_link_trunk} "
        f"{','.join(body.peer_link_members)} uplinks: {_link_text(body.uplinks)}")
    return {"devices": body.leaves, "changes": changes}


@router.get("/devices/{name}/removal", dependencies=[LOGIN])
async def removal_plan(name: str, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """What removing a planned switch would take with it (its M-LAG peer, links on other switches),
    or why it is refused (409)."""
    device = await netbox.find_one("dcim/devices", name=name)
    if device is None:
        raise HTTPException(status_code=404, detail=f"no device {name!r}")
    try:
        plan = await fabric_build.FabricBuilder(netbox, _slug(device.get("site"))).removal(name)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"devices": plan["devices"], "links": plan["links"], "unpair": plan["unpair"]}


@router.delete("/devices/{name}", dependencies=INTENT_WRITE)
async def remove_device(name: str, user: str = Depends(current_user), netbox: NetBoxClient = Depends(get_netbox),
                        accounts: Accounts = Depends(get_accounts)) -> dict:
    """Remove a planned switch (a leaf with its M-LAG peer) from NetBox, with its cables and addresses."""
    device = await netbox.find_one("dcim/devices", name=name)
    if device is None:
        raise HTTPException(status_code=404, detail=f"no device {name!r}")
    return await _segment_call(fabric_build.FabricBuilder(netbox, _slug(device.get("site"))).remove(name),
                               accounts, user, "fabric.remove-device", name)
