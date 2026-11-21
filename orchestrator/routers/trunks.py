"""Server Eth-Trunks."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator import segments as segs
from orchestrator import trunks as trk
from orchestrator.auth import Accounts
from orchestrator.netbox import NetBoxError
from orchestrator.deps import current_user, get_accounts, get_trunks, INTENT_WRITE, LOGIN, _segment_call

router = APIRouter()


@router.get("/devices/{name}/trunks", dependencies=[LOGIN])
async def list_trunks(name: str, trunks: trk.Trunks = Depends(get_trunks)) -> dict:
    """A device's Eth-Trunks, its free ports and (on a pair) the ports free on both devices."""
    try:
        return await trunks.of_device(name)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


class NewTrunk(BaseModel):
    number: int
    members: list[str]
    mlag: bool = False   # an M-LAG trunk: the same trunk on both devices of the pair


@router.post("/devices/{name}/trunks", status_code=201, dependencies=INTENT_WRITE)
async def create_trunk(
    name: str, trunk: NewTrunk, user: str = Depends(current_user), trunks: trk.Trunks = Depends(get_trunks),
    accounts: Accounts = Depends(get_accounts),
) -> dict:
    return await _segment_call(trunks.create(name, trunk.number, trunk.members, trunk.mlag), accounts, user,
                               "trunk.create", f"{name} Eth-Trunk{trunk.number} {'m-lag' if trunk.mlag else 'single'} "
                                               f"members={','.join(trunk.members)}")


@router.delete("/devices/{name}/trunks/{trunk}", dependencies=INTENT_WRITE)
async def delete_trunk(
    name: str, trunk: str, user: str = Depends(current_user), trunks: trk.Trunks = Depends(get_trunks),
    accounts: Accounts = Depends(get_accounts),
) -> dict:
    return await _segment_call(trunks.delete(name, trunk), accounts, user, "trunk.delete", f"{name} {trunk}")
