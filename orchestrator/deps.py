"""Dependencies and helpers shared by the API routers: the clients, the signed-in account, roles,
fabric limits and the intent-write check."""

import asyncio

from fastapi import Cookie, Depends, HTTPException, Request

from orchestrator.annet import AnnetRunner
from orchestrator import db, deploys, drift, jobs as diff_jobs, routing as rt, segments as segs
from orchestrator import imports as imp, routes as rv
from orchestrator import backups, drafts, images, intent_snapshots, intent_store, snmp_passwords, upgrades
from orchestrator.access import Access
from orchestrator.first_setup import FirstSetup
from orchestrator.platform_settings import PlatformSettings
from orchestrator import (ztp as zt, lifecycle as life, locations as loc, routing_import, routing_intent, trunks as trk)
from orchestrator.auth import audit, role_allows
from orchestrator.auth import COOKIE, Accounts
from orchestrator.netbox import NetBoxClient, NetBoxError
from orchestrator.prometheus import PrometheusClient


# A device counts as collected if its last successful poll is at most this old (seconds).
COLLECTED_MAX_AGE = 120


async def get_netbox(request: Request) -> NetBoxClient:
    if (replay := drafts.REPLAY.get()) is not None and replay.netbox is not None:
        return replay.netbox   # a change set's step tried on its copy
    header = request.headers.get(drafts.HEADER)
    if header is not None and request.method in ("GET", "HEAD") and getattr(request.app.state, "drafts", True):
        try:   # draft mode: the fabric as the change set would make it
            return await _resolved(request, get_drafts).copy(_change_set_number(header))
        except drafts.DraftError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
    return request.app.state.netbox


def _change_set_number(header: str) -> int:
    if not header.isdigit():
        raise HTTPException(status_code=400, detail=f"{drafts.HEADER} must be a change set number")
    return int(header)


async def _netbox(request: Request) -> NetBoxClient:
    """get_netbox outside FastAPI's solver."""
    value = _resolved(request, get_netbox)
    return await value if asyncio.iscoroutine(value) else value


def get_annet(request: Request) -> AnnetRunner:
    return request.app.state.annet


def get_prometheus(request: Request) -> PrometheusClient | None:
    return request.app.state.prometheus


def _needs_database(value):
    if value is None:
        raise HTTPException(status_code=503, detail="platform database is not configured (DATABASE_URL)")
    return value


def get_jobs(request: Request) -> diff_jobs.DiffJobs:
    return _needs_database(request.app.state.jobs)


def get_accounts(request: Request) -> Accounts:
    return _needs_database(request.app.state.accounts)


def get_drift(request: Request) -> drift.DriftScheduler | None:
    return getattr(request.app.state, "drift", None)   # None: no database, no scheduler


def get_deploys(request: Request) -> deploys.DeployJobs:
    return _needs_database(request.app.state.deploys)


def get_upgrades(request: Request) -> upgrades.UpgradeJobs:
    return _needs_database(request.app.state.upgrades)


def get_password_jobs(request: Request) -> snmp_passwords.PasswordJobs:
    return _needs_database(request.app.state.snmp_passwords)


def get_segments(netbox: NetBoxClient = Depends(get_netbox)) -> segs.Segments:
    return segs.Segments(netbox)


def get_routing(netbox: NetBoxClient = Depends(get_netbox)) -> rt.Routing:
    return rt.Routing(netbox)


async def current_account(
    session: str | None = Cookie(default=None, alias=COOKIE),
    accounts: Accounts = Depends(get_accounts),
) -> db.User:
    """The logged-in user; every endpoint except /health, /auth/login and /setup needs one
    (/metrics takes Prometheus' token instead)."""
    if (replay := drafts.REPLAY.get()) is not None:
        intent_store.AUTHOR.set(replay.account.username)
        return replay.account   # a change set's step, replayed as its author
    user = await accounts.user_for(session)
    if user is None:
        raise HTTPException(status_code=401, detail="login required")
    intent_store.AUTHOR.set(user.username)   # recorded with the intent document versions it writes
    return user


async def current_user(account: db.User = Depends(current_account)) -> str:
    return account.username


def require(role: str):
    """A route dependency: the logged-in user's role is at least `role` (viewer < operator < approver < admin)."""
    async def check(account: db.User = Depends(current_account)) -> None:
        if not role_allows(account.role, role):
            raise HTTPException(status_code=403, detail=f"needs the {role} role (you are {account.role})")
    return Depends(check)


def _resolved(request: Request, getter):
    """A dependency's value outside FastAPI's solver (tests override the getters)."""
    override = request.app.dependency_overrides.get(getter)
    return override() if override else getter(request)


def get_access(account: db.User = Depends(current_account)) -> Access:
    return Access.of(account)


# Requests that cannot name a fabric of their own: creating, importing, the inventory.
_NO_FABRIC = ("/imports", "/fabrics", "/fabrics/address-plan", "/inventory")


# Fabric-wide reads and job requests that a limited account must aim at one of its fabrics.
_ONE_FABRIC = {("GET", "/topology"), ("GET", "/overview"), ("GET", "/traffic"),
               ("POST", "/diff-jobs"), ("POST", "/deploy-jobs")}


async def _named(request: Request, route: str) -> tuple[set[str], list[str]]:
    """The fabrics and switches a request names (path, query, body)."""
    try:   # JSON bodies only: an image upload is a stream of hundreds of megabytes
        body = await request.json() if request.method in ("POST", "PUT", "PATCH") and \
            request.headers.get("content-type", "").startswith("application/json") and await request.body() else None
    except ValueError:
        body = None
    body = body if isinstance(body, dict) else {}
    sites = {s for s in (request.path_params.get("site"), request.query_params.get("site"), body.get("site"))
             if isinstance(s, str) and s}
    devices = [d for d in [body.get("device")] + list(body.get("devices") or []) if isinstance(d, str)]
    if route.startswith("/devices/{name}"):
        devices.append(request.path_params["name"])
    return sites, devices


async def _fabric_guard(request: Request, access: Access = Depends(get_access)) -> None:
    """A limited account reaches only its fabrics: every fabric a request names (path,
    query, body, a device's or a job's) must be one of them. Lists are filtered by their endpoints."""
    if not access.limited:
        return
    route = getattr(request.scope.get("route"), "path", request.url.path)
    method = request.method
    if route.startswith("/imports") or (method != "GET" and (route in _NO_FABRIC or route.startswith("/inventory"))):
        raise HTTPException(status_code=403, detail="your account is limited to some fabrics: creating or importing "
                                                    "fabrics and changing the inventory is for approvers and admins")
    sites, devices = await _named(request, route)
    if (method, route) in _ONE_FABRIC and not sites and not devices:
        raise HTTPException(status_code=400, detail="choose one of your fabrics")
    netbox = await _netbox(request)
    for name in devices:
        try:
            device = await netbox.find_one("dcim/devices", name=name)
        except NetBoxError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        if device is None or not access.sees(_slug(device.get("site"))):
            raise HTTPException(status_code=404, detail=f"no switch {name!r} in your fabrics")
    if "job_id" in request.path_params:
        jobs = _resolved(request, get_deploys if route.startswith("/deploy-jobs") else get_jobs)
        async with jobs.sessions() as session:
            job = await session.get(db.Job, int(request.path_params["job_id"]))
        # A drift run spans every fabric: its diff is shown with only this account's switches.
        if job is not None and not (job.site is None and job.kind != "deploy") and not access.sees(job.site):
            raise HTTPException(status_code=404, detail=f"job {job.id} not found")
    for site in sites:
        if not access.sees(site):
            raise HTTPException(status_code=403, detail=f"{site} is not one of your fabrics")


async def _logged_in(user: str = Depends(current_user), _: None = Depends(_fabric_guard)) -> str:
    return user


async def _visible_devices(netbox: NetBoxClient, access: Access) -> set[str] | None:
    """The switches of a limited account's fabrics; None: every switch."""
    if not access.limited:
        return None
    found = await asyncio.gather(*(netbox.find("dcim/devices", site=site) for site in access.fabrics))
    return {d["name"] for devices in found for d in devices}


# On route decorators, so the login and role are checked before any other dependency; also the
# fabrics of a limited account.
LOGIN = Depends(_logged_in)          # any role: read only


OPERATE = require("operator")        # intent changes, diff jobs, deploy requests


APPROVE = require("approver")        # approving deploys


ADMIN = require("admin")             # accounts, settings, fabric deletion


def _slug(value) -> str | None:
    if isinstance(value, dict):
        return value.get("value") or value.get("slug") or value.get("name")
    return value


# -- drift ------------------------------------------------------------------------

async def _drift_report(jobs: diff_jobs.DiffJobs, netbox: NetBoxClient) -> list[dict]:
    try:
        devices = await netbox.find("dcim/devices", status="active", has_primary_ip="true")
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return await drift.drift_report(jobs, devices)


def get_setup(request: Request) -> FirstSetup:
    return request.app.state.setup


# -- settings (admins) -------------------------------------------------------------------

def get_settings(request: Request) -> PlatformSettings:
    return _needs_database(request.app.state.settings)


# -- switch software images -------------------------------------------------------------------

def get_images(request: Request) -> images.ImageStore:
    return _needs_database(request.app.state.images)


async def _deploy_approval(job_id: int, request: Request, access: Access = Depends(get_access)) -> None:
    """Approvers, or an operator an admin lets approve this fabric's deploys."""
    if access.may_approve(None):        # approvers and admins: every fabric
        return
    site = None
    if access.limited:
        async with _resolved(request, get_deploys).sessions() as session:
            job = await session.get(db.Job, job_id)
        site = job.site if job else None
    if not access.may_approve(site):
        raise HTTPException(status_code=403, detail="approving this deploy needs the approver role"
                                                    + (f" or approval rights on {site}" if site else "")
                                                    + f" (you are {access.role})")


# -- segments (intent written to NetBox) -------------------------------------------------

def get_drafts(request: Request) -> drafts.Drafts:
    return _needs_database(request.app.state.drafts)


async def _intent_unfrozen(request: Request, account: db.User = Depends(current_account),
                           jobs: deploys.DeployJobs = Depends(get_deploys)) -> None:
    """Refuse intent changes during a rollout (see deploys.ROLLING_OUT). A write sent with
    `X-Change-Set` is saved for a window instead (orchestrator/drafts.py)."""
    route = getattr(request.scope.get("route"), "path", request.url.path)
    if (replay := drafts.REPLAY.get()) is not None:
        if replay.netbox is not None:   # tried on a copy: it must stay within the change set's fabric
            sites, devices = await _named(request, route)
            for name in devices:
                device = await replay.netbox.find_one("dcim/devices", name=name)
                sites.add(_slug(device.get("site")) if device else None)
            if other := sites - {replay.site, None}:
                raise HTTPException(status_code=400, detail=f"this change set is for fabric {replay.site}, "
                                                            f"not {', '.join(sorted(other))}")
        return   # a change set's step: its window's own job is rolling out
    if (header := request.headers.get(drafts.HEADER)) is not None:
        if (request.method, route) in drafts.NOT_STAGED or route.startswith("/dci"):
            raise HTTPException(status_code=400, detail="this change cannot be saved for a window")
        number = _change_set_number(header)
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        try:
            raise await _resolved(request, get_drafts).stage(number, account, request.method, path, await request.body())
        except drafts.StepRefused as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc))
        except drafts.DraftError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    if (job_id := await deploys.rolling_out(jobs.sessions)) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"deploy #{job_id} is rolling out; changes are possible again once it finishes",
        )


INTENT_WRITE = [LOGIN, OPERATE, Depends(_intent_unfrozen)]


async def _segment_call(call, accounts: Accounts, user: str, action: str, detail: str):
    try:
        result = await call
    except segs.SegmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await audit(accounts.sessions, user, action, detail=detail)
    return result


# -- switch lifecycle ---------------------------------------------------------------------

def get_lifecycle(netbox: NetBoxClient = Depends(get_netbox)) -> life.Lifecycle:
    return life.Lifecycle(netbox)


# -- zero-touch provisioning ----------------------------------------------------------------

def get_ztp(netbox: NetBoxClient = Depends(get_netbox)) -> zt.Ztp:
    return zt.Ztp(netbox)


# -- location -----------------------------------------------------------------------------

def get_locations(netbox: NetBoxClient = Depends(get_netbox)) -> loc.Locations:
    return loc.Locations(netbox)


# -- config backups ------------------------------------------------------------------------

def get_backups(request: Request) -> backups.Backups:
    return _needs_database(request.app.state.backups)


def get_snapshots(request: Request) -> intent_snapshots.IntentSnapshots:
    return _needs_database(request.app.state.snapshots)


def get_rollups(request: Request):
    return _needs_database(getattr(request.app.state, "rollups", None))


def get_events(request: Request):
    return _needs_database(getattr(request.app.state, "events", None))


# -- Eth-Trunks ------------------------------------------------------------------------

def get_trunks(netbox: NetBoxClient = Depends(get_netbox)) -> trk.Trunks:
    return trk.Trunks(netbox)


# -- general routing: BGP peers, policies, filters, aggregates, static routes, OSPF --------

def get_routing_store(netbox: NetBoxClient = Depends(get_netbox)) -> routing_intent.RoutingStore:
    return routing_intent.RoutingStore(netbox)


def _microseg_reader(netbox: NetBoxClient = Depends(get_netbox)) -> rv.RouteReader:
    return rv.RouteReader(netbox)


def get_importer(request: Request) -> routing_import.RoutingImporter:
    return _needs_database(getattr(request.app.state, "importer", None))


# -- route view -----------------------------------------------------------------------------

def get_route_reader(netbox: NetBoxClient = Depends(get_netbox)) -> rv.RouteReader:
    return rv.RouteReader(netbox)


def get_clickhouse(request: Request):
    found = getattr(request.app.state, "clickhouse", None)
    if found is None:
        raise HTTPException(status_code=503, detail="the flows store is not configured (CLICKHOUSE_PASSWORD)")
    return found


# -- import an existing fabric ----------------------------------------------------------------

def get_imports(request: Request) -> imp.Imports | None:
    return getattr(request.app.state, "imports", None)   # None only in tests that never start the app
