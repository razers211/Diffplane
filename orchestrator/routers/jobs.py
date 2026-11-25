"""Deploy and upgrade jobs, the audit trail and change sets (drafts for a window)."""

import re
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from orchestrator import db, deploys
from orchestrator import drafts, upgrades
from orchestrator.access import Access
from orchestrator.auth import audit
from orchestrator.auth import Accounts
from orchestrator.netbox import NetBoxClient, NetBoxError
from orchestrator.deps import (
    current_user, _deploy_approval, get_access, get_accounts, get_deploys, get_drafts, get_netbox,
    get_upgrades, LOGIN, OPERATE, _resolved, _visible_devices,
)
from orchestrator.answers import DeployJob, JobId


router = APIRouter()


# -- deploys ----------------------------------------------------------------------------

@router.post("/deploy-jobs", status_code=202, dependencies=[LOGIN, OPERATE], response_model=JobId, response_model_exclude_unset=True)
async def request_deploy(
    user: str = Depends(current_user),
    site: str | None = Body(default=None),
    devices: list[str] | None = Body(default=None),
    generators: list[str] | None = Body(default=None),
    jobs: deploys.DeployJobs = Depends(get_deploys),
) -> dict:
    """Compute patches for approval. Nothing is sent to devices until approved."""
    try:
        return {"id": await jobs.request(user, site=site, devices=devices, generators=generators)}
    except deploys.DeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


def _window(window_start: datetime | None, window_end: datetime | None) -> tuple[datetime, datetime] | None:
    if (window_start is None) != (window_end is None):
        raise HTTPException(status_code=400, detail="a window needs both its start and its end")
    return (window_start, window_end) if window_start else None


@router.post("/deploy-jobs/{job_id}/approve", dependencies=[LOGIN, OPERATE, Depends(_deploy_approval)], response_model=JobId, response_model_exclude_unset=True)
async def approve_deploy(
    job_id: int, user: str = Depends(current_user), jobs: deploys.DeployJobs = Depends(get_deploys),
    window_start: datetime | None = Body(default=None), window_end: datetime | None = Body(default=None),
) -> dict:
    """Approve the shown patches and start the rollout now, or in a change window."""
    try:
        await jobs.approve(job_id, user, _window(window_start, window_end))
    except deploys.DeployError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"id": job_id}


async def _cancel_right(job_id: int, request: Request, user: str = Depends(current_user),
                        access: Access = Depends(get_access)) -> None:
    """The requester, approvers and admins may cancel a job."""
    async with _resolved(request, get_deploys).sessions() as session:
        job = await session.get(db.Job, job_id)
    if job is not None and (job.requested_by == user or access.may_approve(job.site)):
        return
    if job is not None:
        raise HTTPException(status_code=403, detail="only who requested it or an approver may cancel this job")


@router.post("/deploy-jobs/{job_id}/cancel", dependencies=[LOGIN, OPERATE, Depends(_cancel_right)], response_model=JobId, response_model_exclude_unset=True)
async def cancel_deploy(
    job_id: int, user: str = Depends(current_user), jobs: deploys.DeployJobs = Depends(get_deploys)
) -> dict:
    """Cancel a scheduled deploy, or stop a running one after its wave."""
    try:
        await jobs.cancel(job_id, user)
    except deploys.DeployError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"id": job_id}


@router.get("/deploy-jobs", dependencies=[LOGIN], response_model=list[DeployJob], response_model_exclude_unset=True)
async def list_deploy_jobs(
    limit: int = 20, jobs: deploys.DeployJobs = Depends(get_deploys), access: Access = Depends(get_access)
) -> list[dict]:
    return [j for j in await deploys.list_deploys(jobs.sessions, min(max(limit, 1), 200)) if access.sees(j["site"])]


@router.get("/deploy-jobs/{job_id}", dependencies=[LOGIN], response_model=DeployJob, response_model_exclude_unset=True)
async def get_deploy_job(
    job_id: int, jobs: deploys.DeployJobs = Depends(get_deploys)
) -> dict:
    job = await deploys.get_deploy(jobs.sessions, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"deploy job {job_id} not found")
    return job


# -- switch software upgrades: jobs like deploys, a plan per switch instead of a patch ------

@router.post("/upgrade-jobs", status_code=202, dependencies=[LOGIN, OPERATE])
async def request_upgrade(
    user: str = Depends(current_user), devices: list[str] = Body(), system: int | None = Body(default=None),
    patch: int | None = Body(default=None), jobs: upgrades.UpgradeJobs = Depends(get_upgrades),
) -> dict:
    """Read each switch (display only) and plan its upgrade for approval. Nothing changes until approved."""
    try:
        return {"id": await jobs.request(user, devices, system=system, patch=patch)}
    except deploys.DeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/upgrade-jobs/{job_id}/approve", dependencies=[LOGIN, OPERATE, Depends(_deploy_approval)])
async def approve_upgrade(
    job_id: int, user: str = Depends(current_user), jobs: upgrades.UpgradeJobs = Depends(get_upgrades),
    window_start: datetime | None = Body(default=None), window_end: datetime | None = Body(default=None),
) -> dict:
    """Approve the shown plans (deploy rights) and upgrade one switch at a time, now or in a window."""
    try:
        await jobs.approve(job_id, user, _window(window_start, window_end))
    except deploys.DeployError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"id": job_id}


@router.post("/upgrade-jobs/{job_id}/cancel", dependencies=[LOGIN, OPERATE, Depends(_cancel_right)])
async def cancel_upgrade(
    job_id: int, user: str = Depends(current_user), jobs: upgrades.UpgradeJobs = Depends(get_upgrades)
) -> dict:
    """Cancel a scheduled upgrade, or stop a running one after its current switch."""
    try:
        await jobs.cancel(job_id, user)
    except deploys.DeployError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"id": job_id}


@router.get("/upgrade-jobs", dependencies=[LOGIN])
async def list_upgrade_jobs(
    limit: int = 20, jobs: upgrades.UpgradeJobs = Depends(get_upgrades), access: Access = Depends(get_access)
) -> list[dict]:
    return [j for j in await deploys.list_deploys(jobs.sessions, min(max(limit, 1), 200), "upgrade")
            if access.sees(j["site"])]


@router.get("/upgrade-jobs/{job_id}", dependencies=[LOGIN])
async def get_upgrade_job(job_id: int, jobs: upgrades.UpgradeJobs = Depends(get_upgrades)) -> dict:
    job = await deploys.get_deploy(jobs.sessions, job_id, "upgrade")
    if job is None:
        raise HTTPException(status_code=404, detail=f"upgrade job {job_id} not found")
    return job | {"progress": jobs.progress(job_id)}


@router.get("/audit", dependencies=[LOGIN])
async def audit_trail(
    request: Request, limit: int = 100, accounts: Accounts = Depends(get_accounts),
    access: Access = Depends(get_access), netbox: NetBoxClient = Depends(get_netbox),
) -> list[dict]:
    """Who did what, newest first. A limited account sees the events of its fabrics: a job
    of one of them, or a detail naming one of them or one of their switches. Each version of an
    intent document written is an event too (`intent.document`), seen by the fabrics it is
    assigned to."""
    from sqlalchemy import select

    limit = min(max(limit, 1), 1000)
    async with accounts.sessions() as session:
        rows = (await session.scalars(
            select(db.AuditEvent).order_by(db.AuditEvent.id.desc()).limit(limit)
        )).all()
        sites = dict((await session.execute(select(db.Job.id, db.Job.site).where(
            db.Job.id.in_({r.job_id for r in rows if r.job_id})))).all()) if access.limited else {}
    if access.limited:
        names = set(access.fabrics) | await _visible_devices(netbox, access)
        words = lambda text: set(re.split(r"[\s:=,+/]+", text or ""))   # noqa: E731
        rows = [r for r in rows if (access.sees(sites.get(r.job_id)) if r.job_id else words(r.detail) & names)]
    events = [
        {"at": r.at.isoformat(), "user": r.username, "action": r.action, "job_id": r.job_id, "detail": r.detail}
        for r in rows
    ]
    if (store := getattr(request.app.state, "intent_store", None)) is not None:
        events += [
            {"at": v.written_at.isoformat(), "user": v.written_by, "action": "intent.document", "job_id": None,
             "detail": f"{v.name} version {v.version}" + (" (deleted)" if v.data is None else "")}
            for v in await store.newest(limit) if not access.limited or any(access.sees(s) for s in v.sites)
        ]
    return sorted(events, key=lambda e: e["at"], reverse=True)[:limit]


# -- change sets: intent changes saved for a change window ------------------------------

async def _change_set(site: str, change_set_id: int, store: drafts.Drafts = Depends(get_drafts)) -> dict:
    try:
        change_set = await store.get(change_set_id)
    except drafts.DraftError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if change_set["site"] != site:
        raise HTTPException(status_code=404, detail=f"change set {change_set_id} is not of fabric {site}")
    return change_set


@router.post("/fabrics/{site}/change-sets", status_code=201, dependencies=[LOGIN, OPERATE])
async def create_change_set(site: str, user: str = Depends(current_user),
                            store: drafts.Drafts = Depends(get_drafts)) -> dict:
    """A new, empty change set: intent writes sent with `X-Change-Set: <id>` are saved in it."""
    return await store.create(site, user)


@router.get("/fabrics/{site}/change-sets", dependencies=[LOGIN])
async def list_change_sets(site: str, store: drafts.Drafts = Depends(get_drafts)) -> list[dict]:
    return await store.list(site)


@router.get("/fabrics/{site}/change-sets/{change_set_id}", dependencies=[LOGIN])
async def get_change_set(change_set: dict = Depends(_change_set)) -> dict:
    return change_set


@router.post("/fabrics/{site}/change-sets/{change_set_id}/deploy", status_code=201, dependencies=[LOGIN, OPERATE])
async def deploy_change_set(change_set: dict = Depends(_change_set), user: str = Depends(current_user),
                            jobs: deploys.DeployJobs = Depends(get_deploys)) -> dict:
    """Prepare the change set's deploy: each switch's patch with it applied, to approve for a window.
    The change set cannot change any more unless that job ends without applying it."""
    try:
        job_id = await jobs.request_draft(user, change_set["id"])
    except deploys.DeployError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"job_id": job_id}


@router.delete("/fabrics/{site}/change-sets/{change_set_id}", dependencies=[LOGIN, OPERATE])
async def discard_change_set(change_set: dict = Depends(_change_set), user: str = Depends(current_user),
                             access: Access = Depends(get_access), store: drafts.Drafts = Depends(get_drafts),
                             accounts: Accounts = Depends(get_accounts)) -> dict:
    """Throw an open change set away (its author, approvers and admins); nothing was applied."""
    if change_set["author"] != user and not access.may_approve(change_set["site"]):
        raise HTTPException(status_code=403, detail="only its author or an approver may discard a change set")
    try:
        result = await store.set_status(change_set["id"], "discarded", ("open",))
    except drafts.DraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await audit(accounts.sessions, user, "change-set.discard", detail=f"{change_set['site']} #{change_set['id']}")
    return result
