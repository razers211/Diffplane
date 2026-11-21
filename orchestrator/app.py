"""The orchestrator's FastAPI application: start-up, middleware, the API routers and the GUI."""

import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
import asyncio

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles

from orchestrator.annet import AnnetRunner
from orchestrator import db, deploys, drift, hostkeys, jobs as diff_jobs
from orchestrator import imports as imp, routes as rv
from orchestrator import (alerting, backups, drafts, images, intent_snapshots, ospf_auth, snmp_passwords, switch_login, upgrades)
from orchestrator.directory import Directory
from orchestrator.first_setup import FirstSetup
from orchestrator.platform_settings import PlatformSettings
from orchestrator import ztp as zt, routing_import
from orchestrator.auth import Accounts, Throttle
from orchestrator.config import Settings
from orchestrator.migrate import upgrade
from orchestrator.netbox import NetBoxClient, NetBoxError
from orchestrator.prometheus import PrometheusClient
from orchestrator import intent_store, switch_inventory, timing
from orchestrator.deps import COLLECTED_MAX_AGE, _drift_report, _intent_unfrozen

# Re-exported: tests override these dependencies through this module.
from orchestrator.deps import (  # noqa: F401
    ADMIN, APPROVE, current_account, current_user, get_accounts, get_annet, get_backups, get_clickhouse,
    get_deploys, get_drafts, get_drift, get_events, get_images, get_imports, get_jobs, get_netbox,
    get_prometheus, get_rollups, get_route_reader, get_settings, get_setup, get_snapshots,
    _microseg_reader, OPERATE,
)
from orchestrator.routers import (
    accounts, addressing, dc_links, devices, diffs, fabric_features, fabrics, history, imports, jobs,
    policies, reads, routing, segments, status, switches, trunks,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    app.state.netbox = NetBoxClient(
        settings.netbox_url, settings.netbox_token, settings.netbox_timeout
    )
    guard = hostkeys.Guard(app.state.netbox)
    app.state.annet = AnnetRunner(before_connect=guard)
    if guard.path:   # for manual `annet` runs in the container too; refreshed before every run
        try:
            await hostkeys.write_known_hosts(app.state.netbox, Path(guard.path))
        except NetBoxError as exc:
            print(f"cannot write {guard.path} yet: {exc}", file=sys.stderr)
    app.state.prometheus = PrometheusClient(settings.prometheus_url) if settings.prometheus_url else None
    from flows.store import ClickHouse   # the flows pages read what the flows service writes
    app.state.clickhouse = ClickHouse(os.environ.get("CLICKHOUSE_URL", "http://clickhouse:8123"),
                                      os.environ.get("CLICKHOUSE_USER", "flows"), os.environ["CLICKHOUSE_PASSWORD"]) \
        if os.environ.get("CLICKHOUSE_PASSWORD") else None
    app.state.imports = imp.Imports()   # import sessions: in memory only, they hold running configs
    app.state.jobs = app.state.accounts = app.state.deploys = app.state.drift = app.state.importer = None
    app.state.backups = app.state.backup_scheduler = app.state.snapshots = None
    app.state.settings = app.state.images = app.state.upgrades = app.state.drafts = app.state.snmp_passwords = None
    app.state.events = app.state.event_watcher = app.state.rollups = app.state.anomalies = None
    app.state.intent_store = None
    engine = None
    if settings.database_url:
        await asyncio.to_thread(upgrade, settings.database_url)
        engine = db.make_engine(settings.database_url)
        sessions = db.make_sessionmaker(engine)
        app.state.jobs = diff_jobs.DiffJobs(sessions, app.state.annet, app.state.netbox)
        app.state.accounts = Accounts(sessions, directory=Directory())   # AD and RADIUS sign-in
        # GUI settings over the .env defaults, before anything below reads them.
        app.state.settings = PlatformSettings(sessions)
        app.state.images = images.ImageStore(sessions)   # switch software
        await app.state.settings.load()
        # Intent documents: the database is their source, NetBox holds the published copy.
        app.state.intent_store = intent_store.IntentStore(sessions)
        try:
            for line in await app.state.intent_store.start(app.state.netbox):
                print(f"intent store: {line}", file=sys.stderr, flush=True)
        except NetBoxError as exc:   # attached all the same, unless NetBox's contexts were never adopted
            print(f"intent store: {exc}; python -m orchestrator.intent_store republish once NetBox answers",
                  file=sys.stderr, flush=True)
        if problem := await alerting.apply(app.state.settings.env):   # Alertmanager's receiver from the settings
            print(f"alerts: {problem}", file=sys.stderr, flush=True)
        verifier = deploys.Verifier(app.state.annet, app.state.prometheus, app.state.netbox)
        app.state.deploys = deploys.DeployJobs(sessions, app.state.annet, app.state.netbox, verifier)
        app.state.importer = routing_import.RoutingImporter(app.state.netbox, app.state.annet, sessions)
        app.state.jobs.importer = app.state.deploys.importer = app.state.importer
        app.state.backups = backups.Backups(sessions, app.state.annet, app.state.netbox)
        app.state.deploys.backups = app.state.backups
        app.state.snapshots = intent_snapshots.IntentSnapshots(sessions, app.state.netbox)
        app.state.deploys.snapshots = app.state.snapshots
        app.state.drafts = drafts.Drafts(sessions, app.state.netbox, app)   # staged changes
        app.state.deploys.drafts, app.state.deploys.draft_patches = app.state.drafts, drafts.subprocess_patches
        from orchestrator import checks as fabric_checks
        app.state.deploys.checker = lambda site, nb=None: fabric_checks.check_fabric(
            nb or app.state.netbox, app.state.prometheus, site)
        if problem := app.state.backups.problem():
            print(f"backups: {problem}", file=sys.stderr, flush=True)
        app.state.backup_scheduler = backups.BackupScheduler(app.state.backups)
        app.state.backup_scheduler.start()
        await app.state.jobs.recover()
        await app.state.deploys.recover()
        app.state.upgrades = upgrades.UpgradeJobs(app.state.deploys, app.state.images)
        await app.state.upgrades.recover()
        app.state.snmp_passwords = snmp_passwords.PasswordJobs(app.state.deploys, rv.RouteReader(app.state.netbox))
        await app.state.snmp_passwords.recover()
        await snmp_passwords.load(sessions)
        await ospf_auth.load(sessions)
        await switch_login.load(sessions)
        app.state.deploys.reader = app.state.snmp_passwords.reader
        app.state.windows = deploys.WindowScheduler(app.state.deploys, app.state.upgrades)
        app.state.windows.start()
        app.state.drift = drift.DriftScheduler(app.state.jobs)
        app.state.drift.start()
        from orchestrator import events as timeline
        app.state.events = timeline.EventStore(sessions)
        app.state.deploys.events = app.state.upgrades.events = app.state.events   # change windows on the timeline
        if app.state.prometheus is not None:
            app.state.event_watcher = timeline.EventWatcher(app.state.events, app.state.netbox, app.state.prometheus,
                                                            lambda: _drift_report(app.state.jobs, app.state.netbox),
                                                            COLLECTED_MAX_AGE)
            app.state.event_watcher.start()
            from orchestrator import capacity
            app.state.rollups = capacity.Rollups(sessions, app.state.netbox, app.state.prometheus)
            app.state.rollups.start()
            from orchestrator import anomaly
            app.state.anomalies = anomaly.Anomalies(sessions, app.state.netbox, app.state.prometheus)
            app.state.anomalies.start()
        app.state.inventory_sweep = switch_inventory.Sweep(sessions, app.state.netbox)
        app.state.inventory_sweep.start()
    app.state.setup = FirstSetup()
    app.state.throttle = Throttle()   # failed sign-ins per client address
    await app.state.setup.start(app.state.accounts)   # no account yet: a setup code in the log
    # ZTP: keeps dnsmasq's files in step with NetBox and reports switches that came up.
    app.state.ztp = zt.ZtpWatcher(app.state.netbox) if os.environ.get("ZTP_DIR") else None
    if app.state.ztp is not None:
        app.state.ztp.start()
    yield
    if app.state.ztp is not None:
        await app.state.ztp.stop()
        await app.state.inventory_sweep.stop()
    if app.state.drift is not None:
        await app.state.drift.stop()
    if getattr(app.state, "windows", None) is not None:
        await app.state.windows.stop()
    if app.state.event_watcher is not None:
        await app.state.event_watcher.stop()
    if app.state.rollups is not None:
        await app.state.rollups.stop()
    if app.state.anomalies is not None:
        await app.state.anomalies.stop()
    if app.state.backup_scheduler is not None:
        await app.state.backup_scheduler.stop()
    if engine is not None:
        await engine.dispose()
    await app.state.annet.close()
    await app.state.netbox.aclose()
    if app.state.prometheus:
        await app.state.prometheus.aclose()
    if app.state.clickhouse is not None:
        await app.state.clickhouse.aclose()


# Requests that act on the live platform even in draft mode: sign-in, jobs and change sets.
LIVE_IN_DRAFT_MODE = ("/auth", "/deploy-jobs", "/upgrade-jobs", "/snmp-password-jobs", "/diff-jobs", "/fabrics/{site}/change-sets")


async def _draft_scope(request: Request) -> None:
    """In draft mode the GUI sends `X-Change-Set` with every request: reads come from the change
    set's copy (get_netbox) and intent writes are saved in it (_intent_unfrozen). Any other write would
    change the live platform behind the draft's back, so it is refused."""
    if request.method in ("GET", "HEAD") or drafts.HEADER not in request.headers or drafts.REPLAY.get() is not None:
        return
    route = request.scope.get("route")
    path = getattr(route, "path", "")
    if path.startswith(LIVE_IN_DRAFT_MODE) or any(d.dependency is _intent_unfrozen for d in getattr(route, "dependencies", [])):
        return
    raise HTTPException(status_code=400, detail="this cannot be saved in a draft for a window: go back to live to do it")


app = FastAPI(title="Diffplane orchestrator", lifespan=lifespan, dependencies=[Depends(_draft_scope)])


@app.exception_handler(drafts.Staged)
async def staged(_: Request, exc: drafts.Staged) -> JSONResponse:
    """An intent write saved in a change set: accepted, not applied."""
    return JSONResponse(status_code=202, content={"change_set": exc.change_set, "step": exc.step, "result": exc.result})


UNTIMED = ("/health", "/metrics", "/ui")


@app.middleware("http")
async def request_timing(request: Request, call_next):
    """One log line per API request: total time and its NetBox, Annet and Prometheus parts
    (orchestrator/timing.py), also sent as a Server-Timing header; and the request's NetBox
    read cache."""
    if request.url.path.startswith(UNTIMED):
        return await call_next(request)
    tally = timing.Tally()
    token = timing.current.set(tally)
    shared = timing.shared.set(request.method == "GET")
    start = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        tally.open = False   # tasks this request started keep the variable, not the cache
        timing.current.reset(token)
        timing.shared.reset(shared)
    total = time.perf_counter() - start
    response.headers["Server-Timing"] = tally.header(total)
    query = f"?{request.url.query}" if request.url.query else ""
    print(f"timing: {request.method} {request.url.path}{query} {response.status_code} {total * 1000:.0f}ms "
          f"{tally.summary()}".rstrip(), file=sys.stderr, flush=True)
    return response


SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
                               "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


@app.middleware("http")
async def same_origin_and_headers(request: Request, call_next):
    """A state-changing request whose browser says it comes from another site is refused (CSRF;
    on top of the SameSite=Strict cookie); every answer carries the security headers."""
    origin = request.headers.get("origin")
    if request.method in UNSAFE_METHODS and origin is not None \
            and urlsplit(origin).netloc != request.headers.get("host"):
        response = JSONResponse(status_code=403, content={"detail": "cross-site request refused"})
    else:
        response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def mount_gui(target: FastAPI, dist: Path) -> bool:
    """Serve the built GUI (gui/dist) at /ui/, if it was built into the image."""
    if not (dist / "index.html").is_file():
        return False
    target.mount("/ui", StaticFiles(directory=dist, html=True), name="gui")

    @target.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    return True


# Route order matters to FastAPI (first match wins): the routers keep app.py's original order.
ROUTERS = (status, diffs, accounts, jobs, segments, switches, devices, history, trunks, addressing, fabrics,
           fabric_features, policies, dc_links, routing, reads, imports)
for _routes in ROUTERS:
    app.include_router(_routes.router)


def api_routes() -> list[APIRoute]:
    """Every API route, in matching order (app.routes shows each included router as one entry)."""
    return [route for module in ROUTERS for route in module.router.routes if isinstance(route, APIRoute)]


mount_gui(app, Path(os.environ.get("GUI_DIST", "gui/dist")))
