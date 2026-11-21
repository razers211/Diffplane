"""Importing an existing fabric."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator import hostkeys, segments as segs
from orchestrator import imports as imp
from orchestrator.auth import audit
from orchestrator.auth import Accounts
from orchestrator.redact import redact
from orchestrator.netbox import NetBoxClient, NetBoxError
from orchestrator.deps import (
    APPROVE, current_user, get_accounts, get_imports, get_netbox, INTENT_WRITE, LOGIN, OPERATE,
)

router = APIRouter()


async def _import_call(call, accounts: Accounts | None = None, user: str | None = None, action: str = "",
                       detail: str = ""):
    try:
        result = await call
    except (segs.SegmentError, hostkeys.HostKeyError) as exc:
        raise HTTPException(status_code=400, detail=redact(str(exc)))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if accounts is not None:
        await audit(accounts.sessions, user, action, detail=detail)
    return result


class ImportStart(BaseModel):
    addresses: list[str]   # management addresses with mask


class ImportApproval(BaseModel):
    address: str
    keys: list[str]        # exactly the keys shown, "<type> <base64>"


class ImportChoices(BaseModel):
    name: str
    pools: dict[str, str] = {}
    gateway_mac: str | None = None
    roles: dict[str, str] = {}
    architecture: str | None = None
    gateway_pair: list[str] | None = None
    drop: list[int] = []   # bridge-domain IDs left out of the import (the first deploy would remove them)


@router.post("/imports", status_code=201, dependencies=[LOGIN, OPERATE])
async def start_import(body: ImportStart, netbox: NetBoxClient = Depends(get_netbox),
                       imports: imp.Imports = Depends(get_imports)) -> dict:
    """Start an import: the host keys each address offers (ports 22 and 830), to compare."""
    return await _import_call(imports.start(netbox, body.addresses))


@router.get("/imports/{import_id}", dependencies=[LOGIN])
async def get_import(import_id: str, imports: imp.Imports = Depends(get_imports)) -> dict:
    async def view():
        return imports.view(import_id, imports._session(import_id))
    return await _import_call(view())


@router.post("/imports/{import_id}/approve", dependencies=[LOGIN, APPROVE])
async def approve_import_keys(import_id: str, body: ImportApproval, user: str = Depends(current_user),
                              imports: imp.Imports = Depends(get_imports),
                              accounts: Accounts = Depends(get_accounts)) -> dict:
    """An approver compared the fingerprints with the switch's console: these keys are trusted."""
    return await _import_call(imports.approve(import_id, body.address, body.keys, user), accounts, user,
                              "import.approve-keys",
                              f"{body.address}: {', '.join(hostkeys.fingerprint(k) for k in body.keys if len(k.split()) > 1)}")


@router.post("/imports/{import_id}/read", dependencies=[LOGIN, OPERATE])
async def read_import(import_id: str, imports: imp.Imports = Depends(get_imports)) -> dict:
    """Read every approved switch over SSH (display commands only)."""
    return await _import_call(imports.read(import_id))


@router.post("/imports/{import_id}/detect", dependencies=[LOGIN, OPERATE])
async def detect_import(import_id: str, imports: imp.Imports = Depends(get_imports)) -> dict:
    """New fabric and Add switches: read each approved switch's model and ports over NETCONF."""
    return await _import_call(imports.detect(import_id))


@router.post("/imports/{import_id}/preview", dependencies=[LOGIN, OPERATE])
async def preview_import(import_id: str, body: ImportChoices, imports: imp.Imports = Depends(get_imports)) -> dict:
    """The fabric the switches give, and the patch each would get; nothing is written."""
    return await _import_call(imports.preview(import_id, body.model_dump()))


@router.post("/imports/{import_id}/write", status_code=201, dependencies=INTENT_WRITE)
async def write_import(import_id: str, body: ImportChoices, user: str = Depends(current_user),
                       netbox: NetBoxClient = Depends(get_netbox), imports: imp.Imports = Depends(get_imports),
                       accounts: Accounts = Depends(get_accounts)) -> dict:
    """Write the imported fabric to NetBox (its switches active and pinned; deploys stay off)."""
    return await _import_call(imports.write(netbox, import_id, body.model_dump()), accounts, user, "fabric.import",
                              body.name)


@router.delete("/imports/{import_id}", dependencies=[LOGIN, OPERATE])
async def drop_import(import_id: str, imports: imp.Imports = Depends(get_imports)) -> dict:
    async def drop():
        imports.drop(import_id)
        return {"dropped": import_id}
    return await _import_call(drop())
