"""DC interconnect links and stretched segments."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator import dci
from orchestrator.auth import Accounts
from orchestrator.netbox import NetBoxClient, NetBoxError
from orchestrator.deps import (
    APPROVE, current_user, get_accounts, get_netbox, INTENT_WRITE, LOGIN, _segment_call,
)

router = APIRouter()


# -- DC links: EVPN DCI between two fabrics' border pairs -----------------
# A link spans two fabrics, so changing one needs an account that reaches every fabric (approver).

class DciUplink(BaseModel):
    port: str
    address: str      # this end, a /30 or /31
    neighbour: str    # the other DC's border (dark fibre) or the provider's PE (L3 VPN)
    asn: int


class DciSide(BaseModel):
    fabric: str
    pair: list[str]
    uplinks: dict[str, list[DciUplink]] = {}


class DciLink(BaseModel):
    sides: list[DciSide]
    split_group: str = "DCI"


class DciSegment(BaseModel):
    vnis: dict[str, int]   # the segment's VNI in each fabric; the DCI VNI is picked by the platform


DCI_WRITE = [*INTENT_WRITE, APPROVE]


@router.get("/dci", dependencies=[LOGIN, APPROVE])
async def get_dci(netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    try:
        return await dci.get(netbox)
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.put("/dci/links/{name}", dependencies=DCI_WRITE)
async def set_dci_link(name: str, body: DciLink, user: str = Depends(current_user),
                       netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    sides = [s.model_dump() for s in body.sides]
    return await _segment_call(dci.set_link(netbox, name, sides, body.split_group), accounts, user, "dci.link",
                               f"{name} {' <-> '.join(s.fabric for s in body.sides)}")


@router.delete("/dci/links/{name}", dependencies=DCI_WRITE)
async def delete_dci_link(name: str, user: str = Depends(current_user),
                          netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    await _segment_call(dci.delete_link(netbox, name), accounts, user, "dci.link", f"delete {name}")
    return {"deleted": name}


@router.put("/dci/links/{name}/segments/{segment}", dependencies=DCI_WRITE)
async def set_dci_segment(name: str, segment: str, body: DciSegment, user: str = Depends(current_user),
                          netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(dci.set_segment(netbox, name, segment, body.vnis), accounts, user,
                               "dci.segment", f"{name} {segment} {body.vnis}")


@router.delete("/dci/links/{name}/segments/{segment}", dependencies=DCI_WRITE)
async def delete_dci_segment(name: str, segment: str, user: str = Depends(current_user),
                             netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    await _segment_call(dci.delete_segment(netbox, name, segment), accounts, user, "dci.segment", f"{name} stop {segment}")
    return {"deleted": segment}
