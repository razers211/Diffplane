"""Diff jobs, drift and the Prometheus metrics endpoint."""

import hmac
import os

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response

from orchestrator import drift, intent_store, jobs as diff_jobs, segments as segs
from orchestrator import faceplate as fp
from orchestrator.access import Access
from orchestrator.netbox import NetBoxClient, NetBoxError
from orchestrator import timing
from orchestrator.deps import (
    current_user, _drift_report, get_access, get_drift, get_jobs, get_netbox, LOGIN, OPERATE,
    _visible_devices,
)

router = APIRouter()


@router.post("/diff-jobs", status_code=202, dependencies=[LOGIN, OPERATE])
async def start_diff_job(
    site: str | None = Body(default=None),
    devices: list[str] | None = Body(default=None),
    jobs: diff_jobs.DiffJobs = Depends(get_jobs), user: str = Depends(current_user),
) -> dict:
    """Run `annet diff` on active devices (of a fabric, or listed). Read-only on devices."""
    try:
        job_id = await jobs.start(site=site, devices=devices, requested_by=user)
    except diff_jobs.JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"id": job_id}


@router.get("/diff-jobs", dependencies=[LOGIN])
async def list_diff_jobs(limit: int = 20, jobs: diff_jobs.DiffJobs = Depends(get_jobs),
                         access: Access = Depends(get_access), user: str = Depends(current_user)) -> list[dict]:
    found = await diff_jobs.list_jobs(jobs.sessions, min(max(limit, 1), 200))
    # A limited account: its fabrics' jobs, and the switch checks it started itself.
    return [j for j in found if access.sees(j["site"]) or (j["site"] is None and j.get("requested_by") == user)] \
        if access.limited else found


@router.get("/diff-jobs/{job_id}", dependencies=[LOGIN])
async def get_diff_job(job_id: int, jobs: diff_jobs.DiffJobs = Depends(get_jobs),
                       access: Access = Depends(get_access), netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    job = await diff_jobs.get_job(jobs.sessions, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    if access.limited and job["site"] is None:   # a drift run or a switch list: only this account's switches
        mine = await _visible_devices(netbox, access)
        job["snapshots"] = [s for s in job["snapshots"] if s["device"] in mine]
        job.update(devices=len(job["snapshots"]), changed=sum(1 for s in job["snapshots"] if s["has_changes"]),
                   errors=sum(1 for s in job["snapshots"] if s["status"] == "error"))
    return job


@router.get("/devices/{name}/diff", dependencies=[LOGIN])
async def device_latest_diff(name: str, jobs: diff_jobs.DiffJobs = Depends(get_jobs),
) -> dict:
    """The device's most recent finished diff snapshot."""
    snap = await diff_jobs.latest_snapshot(jobs.sessions, name)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"no diff for {name!r} yet")
    return snap


@router.get("/drift", dependencies=[LOGIN])
async def drift_status(
    jobs: diff_jobs.DiffJobs = Depends(get_jobs), netbox: NetBoxClient = Depends(get_netbox),
    scheduler: drift.DriftScheduler | None = Depends(get_drift), access: Access = Depends(get_access),
) -> dict:
    """Each active device's latest diff (in sync, out of sync, error, unknown) and the schedule."""
    state = scheduler.state if scheduler else None
    return {
        "scheduler": None if state is None else {
            "interval_seconds": state.interval,
            "last_started": state.last_started,
            "last_job_id": state.last_job_id,
            "last_skip": state.last_skip,
            "next_run": state.next_run,
        },
        "devices": [d for d in await _drift_report(jobs, netbox) if access.sees(d["site"])],
    }


async def _metrics_token(authorization: str | None = Header(default=None)) -> None:
    """Prometheus' scrape: `Authorization: Bearer <METRICS_TOKEN>`. No token set: refused."""
    expected = os.environ.get("METRICS_TOKEN", "")
    if not expected or not hmac.compare_digest((authorization or "").encode(), f"Bearer {expected}".encode()):
        raise HTTPException(status_code=401, detail="metrics token required", headers={"WWW-Authenticate": "Bearer"})


@router.get("/metrics", include_in_schema=False, dependencies=[Depends(_metrics_token)])
async def metrics(
    request: Request, jobs: diff_jobs.DiffJobs = Depends(get_jobs), netbox: NetBoxClient = Depends(get_netbox),
    scheduler: drift.DriftScheduler | None = Depends(get_drift),
) -> Response:
    """Drift, ports in use, what is planned and traffic anomalies and flapping links
    for Prometheus, which sends the metrics token; no diffs in it."""
    from orchestrator import controlplane

    timing.shared.set(True)   # scraped every 30 s by each server's Prometheus
    report = await _drift_report(jobs, netbox)
    body = drift.metrics(report, scheduler.state if scheduler else None)
    try:
        body += fp.in_use_metrics(await fp.ports_in_use(netbox, sorted(r["device"] for r in report)))
    except (NetBoxError, segs.SegmentError):   # drift still exported; optic alerts wait for the next scrape
        pass
    try:
        body += await controlplane.planned_metrics(netbox)   # planned partners never alarm
    except NetBoxError:
        pass
    if (anomalies := getattr(request.app.state, "anomalies", None)) is not None:
        body += anomalies.metrics()
    if (store := getattr(request.app.state, "intent_store", None)) is not None:
        try:
            body += intent_store.metrics(await store.compare(netbox))
        except NetBoxError:
            pass
    return Response(body, media_type="text/plain; version=0.0.4")
