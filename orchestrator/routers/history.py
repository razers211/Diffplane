"""Config backups, intent snapshots and rollback, checks, health, capacity, events."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from orchestrator.annet import AnnetError
from orchestrator import jobs as diff_jobs, segments as segs
from orchestrator import backups, intent_snapshots
from orchestrator.access import Access
from orchestrator.auth import audit
from orchestrator.auth import Accounts
from orchestrator.redact import redact
from orchestrator.netbox import NetBoxClient, NetBoxError
from orchestrator.prometheus import PrometheusClient, PrometheusError
from orchestrator.deps import (
    COLLECTED_MAX_AGE, current_user, _drift_report, get_access, get_accounts, get_backups, get_events,
    get_jobs, get_netbox, get_prometheus, get_rollups, get_snapshots, INTENT_WRITE, LOGIN, OPERATE,
)

router = APIRouter()


async def _backup_call(call):
    try:
        return await call
    except backups.BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except AnnetError as exc:
        raise HTTPException(status_code=502, detail=redact(str(exc)))


@router.get("/devices/{name}/configs", dependencies=[LOGIN])
async def list_config_versions(name: str, store: backups.Backups = Depends(get_backups)) -> dict:
    """The switch's kept running configs, newest first (no text), and whether backups are on."""
    return {"versions": await store.versions(name), "problem": store.problem(),
            "keep_days": backups.KEEP_DAYS, "keep_last": backups.KEEP_LAST}


@router.get("/devices/{name}/configs/{version_id}", dependencies=[LOGIN])
async def show_config_version(name: str, version_id: int, store: backups.Backups = Depends(get_backups)) -> dict:
    """One kept config, secrets masked."""
    return await _backup_call(store.shown(name, version_id))


@router.get("/devices/{name}/configs/{older}/diff/{newer}", dependencies=[LOGIN])
async def diff_config_versions(name: str, older: int, newer: int, store: backups.Backups = Depends(get_backups)) -> dict:
    """A unified diff between two kept configs, secrets masked."""
    return await _backup_call(store.diff(name, older, newer))


@router.post("/devices/{name}/configs", status_code=201, dependencies=[LOGIN, OPERATE])
async def back_up_config(name: str, user: str = Depends(current_user), store: backups.Backups = Depends(get_backups),
                         accounts: Accounts = Depends(get_accounts)) -> dict:
    """Back up the switch's running config now (read-only on the switch)."""
    version = await _backup_call(store.capture(name, "manual", user=user))
    if version["new"]:
        await audit(accounts.sessions, user, "config.backup", detail=f"{name} version {version['id']}")
    return version


@router.get("/fabrics/{site}/intent-snapshots", dependencies=[LOGIN])
async def list_intent_snapshots(site: str, store: intent_snapshots.IntentSnapshots = Depends(get_snapshots)) -> list[dict]:
    """The fabric's rollback points: its intent at each verified deploy, newest first."""
    return await store.list(site)


@router.get("/fabrics/{site}/intent-snapshots/{snapshot_id}", dependencies=[LOGIN])
async def get_intent_snapshot(site: str, snapshot_id: int,
                              store: intent_snapshots.IntentSnapshots = Depends(get_snapshots)) -> dict:
    found = await store.get(site, snapshot_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no rollback point {snapshot_id} of {site}")
    return found


@router.get("/fabrics/{site}/intent-snapshots/{snapshot_id}/rollback", dependencies=[LOGIN])
async def preview_rollback(site: str, snapshot_id: int, device: str | None = None,
                           netbox: NetBoxClient = Depends(get_netbox),
                           store: intent_snapshots.IntentSnapshots = Depends(get_snapshots)) -> dict:
    """The intent operations that would restore the rollback point, for one switch (`device`)
    or the whole fabric; nothing is written."""
    from orchestrator import rollback

    try:
        return await rollback.preview(netbox, store, site, snapshot_id, device)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404 if "no rollback point" in str(exc) else 400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/fabrics/{site}/checks", dependencies=[LOGIN])
async def fabric_checks(site: str, netbox: NetBoxClient = Depends(get_netbox),
                        prometheus: PrometheusClient | None = Depends(get_prometheus)) -> dict:
    """Pre-deploy checks of the fabric's intent now: errors block approval, warnings inform."""
    from orchestrator import checks

    try:
        return await checks.check_fabric(netbox, prometheus, site)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/fabrics/{site}/health", dependencies=[LOGIN])
async def fabric_health(site: str, request: Request, netbox: NetBoxClient = Depends(get_netbox),
                        prometheus: PrometheusClient | None = Depends(get_prometheus),
                        jobs: diff_jobs.DiffJobs = Depends(get_jobs)) -> dict:
    """Health scores of the fabric's switches, links and segments, with their reasons; the
    fabric's score is its worst switch's or link's."""
    from orchestrator import health

    if prometheus is None:
        raise HTTPException(status_code=503, detail="Prometheus is not configured")
    try:
        watcher = getattr(request.app.state, "event_watcher", None)
        if watcher is not None and netbox is request.app.state.netbox and (recent := watcher.recent(site)):
            return recent   # at most two watcher intervals old; not in draft mode
        if await netbox.find_one("dcim/sites", slug=site) is None:
            raise HTTPException(status_code=404, detail=f"no fabric {site!r}")
        facts = await health.facts(netbox, prometheus, site, await _drift_report(jobs, netbox), COLLECTED_MAX_AGE)
        return health.fabric_health(site, facts)
    except PrometheusError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/fabrics/{site}/capacity", dependencies=[LOGIN])
async def fabric_capacity(site: str, days: int = 7, rollups=Depends(get_rollups),
                          netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """Every port and trunk of the fabric with its peak hourly 95th percentile over the last `days`
    (1 to 90) as a share of its speed, and its forecast to 70 %; busiest first."""
    from orchestrator import capacity

    if not 1 <= days <= 90:
        raise HTTPException(status_code=400, detail="days is 1 to 90")
    try:
        return await capacity.fabric_capacity(rollups.sessions, netbox, rollups.prometheus, site, days)
    except (PrometheusError, NetBoxError) as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/devices/{name}/capacity", dependencies=[LOGIN])
async def port_capacity(name: str, interface: str, days: int = 28, rollups=Depends(get_rollups)) -> dict:
    """One port's or trunk's hourly traffic over the last `days` (1 to 395), its daily 95th
    percentiles and its forecast."""
    from orchestrator import capacity

    if not 1 <= days <= 395:
        raise HTTPException(status_code=400, detail="days is 1 to 395")
    try:
        return await capacity.port_capacity(rollups.sessions, rollups.prometheus, name, interface, days)
    except PrometheusError as exc:
        raise HTTPException(status_code=400 if "invalid" in str(exc) else 503, detail=str(exc))


@router.get("/events", dependencies=[LOGIN])
async def timeline(site: str | None = None, device: str | None = None, kind: str | None = None,
                   since: datetime | None = None, until: datetime | None = None, limit: int = 200,
                   store=Depends(get_events), netbox: NetBoxClient = Depends(get_netbox),
                   access: Access = Depends(get_access)) -> list[dict]:
    """What changed, newest first: state changes the event watcher recorded (links, peers,
    hardware, alerts, drift, health) and deploys and intent changes from the audit trail (kind
    `change`). A limited account sees its fabrics only."""
    if site:
        sites: set[str] | None = {site}
    elif access.limited:
        sites = set(access.fabrics)
    else:
        sites = None
    try:
        names = ({d["name"] for s in sites for d in await netbox.find("dcim/devices", site=s)}
                 if sites is not None else None)
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return await store.timeline(sites, device=device, kind=kind, since=since, until=until, limit=limit, names=names)


class RollbackRequest(BaseModel):
    device: str | None = None     # None: the whole fabric
    expected: int                 # how many operations the preview showed


@router.post("/fabrics/{site}/intent-snapshots/{snapshot_id}/rollback", dependencies=INTENT_WRITE)
async def apply_rollback(site: str, snapshot_id: int, body: RollbackRequest, user: str = Depends(current_user),
                         netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts),
                         store: intent_snapshots.IntentSnapshots = Depends(get_snapshots)) -> dict:
    """Restore a rollback point into intent: the switches change only through the usual
    review, approval and deploy. Stops at the first operation the orchestrator refuses."""
    from orchestrator import rollback

    scope = body.device or "fabric"
    try:
        result = await rollback.apply(netbox, store, site, snapshot_id, body.device, body.expected)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=409 if "preview again" in str(exc) else 400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if result["done"]:
        await audit(accounts.sessions, user, "fabric.rollback",
                    detail=f"{site} {scope} to point {snapshot_id}: {len(result['done'])} of "
                           f"{len(result['operations'])} operations")
    if result["failed"]:
        await audit(accounts.sessions, user, "fabric.rollback-stopped",
                    detail=f"{site} {scope} at: {result['failed']['text']}: {result['failed']['error']}")
    return result
