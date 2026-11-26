"""Deploy jobs: patch, approval, then a rollout one device at a time with verification.

States of a job:
    [queued ->] preparing -> diffed (awaiting approval) -> [scheduled ->] approved -> deploying -> verified
    any step can end in failed; a job with nothing to change ends as unchanged; a scheduled or
    rolling-out job can end as cancelled, and a rollout that reaches its window's end as stopped.
    A job requested while another is rolling out is queued: its patches are computed only
    after that rollout ends, so they reflect the switches as that rollout left them.
States of a device step:
    pending -> diffed | unchanged -> deploying -> verifying -> verified | failed; skipped
    when an earlier device failed.

Safety: only devices in DEPLOY_ALLOWED_SITES; one deploy job at a time;
each device's patch is recomputed right before its deploy and must equal the approved
patch; the rollout stops at the first device that fails verification.

Change windows: a job approved for a window waits as scheduled (intent is not
frozen meanwhile) and starts at the window's start, or late while still inside the window; it is
rechecked whole before any switch changes; no new wave starts after the window's end.
"""

import asyncio
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from generators import naming
from generators.switch_login import NAME
from orchestrator.annet import AnnetError, AnnetRunner
from orchestrator.auth import audit
from orchestrator.db import DeployStep, DiffSnapshot, Job, JobLog, now
from orchestrator.netbox import NetBoxClient
from orchestrator.redact import EXTRA, redact
from seed import ports as seed_ports

log = logging.getLogger(__name__)

ACTIVE = ("preparing", "diffed", "approved", "deploying", "reapproval")
# While a job is in one of these, NetBox intent is frozen: `annet deploy` regenerates
# config from NetBox, so a change made during a rollout would be deployed unapproved.
ROLLING_OUT = ("approved", "deploying", "reapproval")
# An upgrade rolls out like a deploy: its reboots expect the intent the diff was empty for.
LOGIN_GENERATOR = "SwitchLogin"


def needs_weak_algorithms(patch: str) -> bool:
    """The patch turns SNMPv2c on (`snmp-agent sys-info version ... v2c`, not `... disable`)."""
    return any(line.strip().startswith("snmp-agent sys-info version") and " v2c" in line
               and not line.rstrip().endswith("disable") for line in patch.splitlines())


def patch_note(patch: str) -> str:
    if not patch:
        return "no changes"
    note = f"{len(patch.splitlines())} patch lines"
    return note + "; `install feature-software WEAKEA` first (SNMPv2c needs it)" if needs_weak_algorithms(patch) else note


def touches_login(patch: str) -> bool:
    """Every patch of that generator names its template, its schemes or a local user."""
    return NAME in patch or "local-user " in patch


ROLLOUT_KINDS = ("deploy", "upgrade", "snmp-password")   # the password job too (one rollout at a time)
# at most this long between approval and a window's start.
MAX_AHEAD = timedelta(days=7)
WINDOW_TICK = 30.0
REMIND_BEFORE = timedelta(minutes=15)   # "window starts in 15 min" to the alert receiver
REAPPROVE_SECONDS = 15 * 60.0   # an approver may approve a window's new patch this long


def window_problem(start: datetime, end: datetime, at: datetime) -> str | None:
    """Why a change window cannot be used, or None."""
    if start.tzinfo is None or end.tzinfo is None:
        return "window times need a time zone"
    if end <= start:
        return "the window must end after it starts"
    if end <= at:
        return "the window is already over"
    if start > at + MAX_AHEAD:
        return "a window may start at most 7 days after approval"
    return None


async def rolling_out(sessions: async_sessionmaker) -> int | None:
    """The deploy or upgrade job being rolled out, if any."""
    async with sessions() as session:
        return await session.scalar(
            select(Job.id).where(Job.kind.in_(ROLLOUT_KINDS), Job.status.in_(ROLLING_OUT)).limit(1)
        )


def allowed_sites() -> set[str]:
    """Fabrics deploys may go to: DEPLOY_ALLOWED_SITES, set per fabric from the Fabric page
    (a platform setting; the .env value is the default). Read when used, so a change applies at once."""
    return {s.strip() for s in os.environ.get("DEPLOY_ALLOWED_SITES", "").split(",") if s.strip()}


def _slug(value) -> str | None:
    return value.get("slug") if isinstance(value, dict) else value


def rollout_waves(names: list[str], devices: list[dict]) -> list[list[str]]:
    """The switches of a rollout in waves deployed at once: never both switches of an
    M-LAG pair in one wave, at most half of a fabric's active switches and half its spines (at least one
    each); each switch goes into the first wave that takes it, in the job's order. `devices`: the active ones."""
    by_name = {d["name"]: d for d in devices}
    names_by_id = {d["id"]: d["name"] for d in devices}

    def site(name):
        return _slug((by_name.get(name) or {}).get("site"))

    def spine(name):
        return _slug((by_name.get(name) or {}).get("role")) == "spine"

    def peer(name):
        ref = ((by_name.get(name) or {}).get("custom_fields") or {}).get("mlag_peer")
        return names_by_id.get(ref.get("id") if isinstance(ref, dict) else ref)

    size = Counter(site(d["name"]) for d in devices)
    spines = Counter(site(d["name"]) for d in devices if spine(d["name"]))
    waves: list[list[str]] = []
    for name in names:
        fabric = site(name)
        for wave in waves:
            here = [w for w in wave if site(w) == fabric]
            if (peer(name) not in wave and len(here) < max(1, size[fabric] // 2)
                    and not (spine(name) and sum(map(spine, here)) >= max(1, spines[fabric] // 2))):
                wave.append(name)
                break
        else:
            waves.append([name])
    return waves


class DeployError(Exception):
    pass


class VerificationFailed(Exception):
    pass


@dataclass
class VerifySettings:
    # The collector polls every 5 s (counters) and 25 s (state); wait for a poll after the deploy.
    collector_timeout: float = 150.0
    poll_interval: float = 5.0


class Verifier:
    """Is the device fine after its deploy? Diff empty, collector polled it, links not lost."""

    def __init__(self, annet: AnnetRunner, prometheus, netbox: NetBoxClient, settings: VerifySettings | None = None):
        self.annet = annet
        self.prometheus = prometheus
        self.netbox = netbox
        self.settings = settings or VerifySettings()

    async def fabric_ports(self, device: str) -> list[str]:
        found = await self.netbox.find_one("dcim/devices", name=device)
        if found is None:
            raise VerificationFailed(f"{device} is not in NetBox")
        ports = await self.netbox.find("dcim/interfaces", device_id=found["id"], cabled="true")
        return sorted(i["name"] for i in ports)

    async def links_up(self, device: str) -> dict[str, bool]:
        """Cabled ports of the device and whether each is up now (unknown ports omitted)."""
        if self.prometheus is None:
            raise VerificationFailed("cannot verify: Prometheus is not configured")
        _, _, oper = await self.prometheus.device_state(device)
        return {port: oper[port] for port in await self.fabric_ports(device) if port in oper}

    async def verify(self, device: str, links_before: dict[str, bool], deployed_at: float) -> None:
        diff = await self.annet.diff(device)
        if diff:
            raise VerificationFailed(f"diff is not empty after the deploy (remaining diff below)\n{redact(diff)}")
        if self.prometheus is None:
            raise VerificationFailed("cannot verify: Prometheus is not configured")
        deadline = time.monotonic() + self.settings.collector_timeout
        while True:
            last, up, _ = await self.prometheus.device_state(device)
            if up and last is not None and last > deployed_at:
                break
            if time.monotonic() > deadline:
                raise VerificationFailed("the collector has not polled the device successfully since the deploy")
            await asyncio.sleep(self.settings.poll_interval)
        after = await self.links_up(device)
        lost = sorted(port for port, was_up in links_before.items() if was_up and not after.get(port, False))
        if lost:
            raise VerificationFailed(f"fabric links down after the deploy: {', '.join(lost)}")


class DeployJobs:
    KIND = "deploy"

    def __init__(self, sessions: async_sessionmaker, annet: AnnetRunner, netbox: NetBoxClient, verifier: Verifier):
        self.sessions = sessions
        self.annet = annet
        self.netbox = netbox
        self.verifier = verifier
        self.reader = None   # NETCONF reads (the trial's login check), set once the app has one
        self._tasks: set[asyncio.Task] = set()
        # hand-made routing objects are imported before patches are computed (set by the app).
        self.importer = None
        # the switch's running config is kept after it is verified, and the fabric's intent
        # once the whole deploy is (set by the app).
        self.backups = None
        self.snapshots = None
        # pre-deploy checks of a fabric (`async (site, netbox=None) -> {"findings", "blocking", ...}`,
        # set by the app; `netbox` a change set's copy).
        self.checker = None
        # change sets (orchestrator/drafts.py) and their patches from a copy of NetBox
        # (`async (site, copy, {switch: running config}) -> {switch: patch}`), set by the app.
        self.drafts = None
        self.draft_patches = None
        self.events = None   # the timeline: window starts, ends and failures go there (set by the app)
        self._lock = asyncio.Lock()
        self._writing = asyncio.Lock()
        self.clock = now                          # the scheduler's time (tests move it)
        self._cancelled: dict[int, str] = {}      # rolling-out job -> who cancelled it
        self._waiting: set[int] = set()           # scheduled jobs already logged as waiting
        self._reapproved: dict[int, asyncio.Event] = {}   # window jobs waiting for an approver
        self.reapprove_timeout = REAPPROVE_SECONDS

    # -- lifecycle -------------------------------------------------------------------

    async def _reschedule_unstarted(self) -> None:
        """A window's job whose start was interrupted before any switch changed goes back to waiting:
        it starts again while still inside its window."""
        async with self.sessions() as session:
            jobs = (await session.scalars(select(Job).where(
                Job.kind == self.KIND, Job.status == "approved", Job.window_start.is_not(None)))).all()
            back = [j.id for j in jobs if all(s.status in ("diffed", "unchanged") for s in j.steps)]
            for job in jobs:
                if job.id in back:
                    job.status = "scheduled"
            await session.commit()
        for job_id in back:
            await self._log(job_id, None, "warn", "the platform restarted before any switch changed: "
                                                  "starting again while the window is open")

    async def recover(self) -> None:
        """Jobs interrupted by a restart cannot resume safely: fail them, loudly."""
        await self._reschedule_unstarted()
        async with self.sessions() as session:
            jobs = (await session.scalars(
                select(Job).where(Job.kind == "deploy", Job.status.in_(ACTIVE))
            )).all()
            for job in jobs:
                for step in job.steps:
                    if step.status in ("deploying", "verifying"):
                        step.status, step.finished_at = "failed", now()
                        step.error = "orchestrator restarted during the deploy; the device may be partly configured"
                    elif step.status in ("pending", "diffed"):
                        step.status = "skipped"
                job.status, job.finished_at = "failed", now()
                job.error = "orchestrator restarted during the job"
            await session.commit()
        # Queued jobs survive a restart (nothing has been computed for them yet).
        await self._drain()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # -- requests ---------------------------------------------------------------------

    async def _targets(self, site: str | None, devices: list[str] | None) -> list[str]:
        found = await self.netbox.find("dcim/devices", status="active", has_primary_ip="true",
                                       **({"site": site} if site else {}))
        by_name = {d["name"]: d for d in found}
        names = sorted(by_name) if devices is None else list(dict.fromkeys(devices))
        unknown = [n for n in names if n not in by_name]
        if unknown:
            raise DeployError(f"not active devices with a management address: {', '.join(unknown)}")
        sites = allowed_sites()
        refused = [n for n in names if (by_name[n].get("site") or {}).get("slug") not in sites]
        if refused:
            raise DeployError(
                f"deploys are only allowed to fabrics {sorted(sites)} (DEPLOY_ALLOWED_SITES): {', '.join(refused)}"
            )
        if not names:
            raise DeployError("no active devices in scope")
        # shortcut: only Huawei generators and deploys exist yet; Arista deploys come with ROADMAP's Arista step 5.
        other = [n for n in names if seed_ports.vendor_of_model((by_name[n].get("device_type") or {}).get("model"))
                 != naming.HUAWEI]
        if other:
            raise DeployError(f"deploys to Arista switches are not built yet: {', '.join(other)}")
        return names

    async def request(self, user: str, site: str | None = None, devices: list[str] | None = None,
                      generators: list[str] | None = None) -> int:
        targets = await self._targets(site, devices)
        async with self._lock:  # with _drain: a job is either queued or prepared, never lost
            running = await rolling_out(self.sessions)
            async with self.sessions() as session:
                job = Job(kind="deploy", status="queued" if running else "preparing", site=site,
                          requested_by=user, generators=",".join(generators) if generators else None)
                job.steps = [DeployStep(device=d, position=i, status="pending") for i, d in enumerate(targets)]
                session.add(job)
                await session.commit()
                job_id = job.id
        await audit(self.sessions, user, "deploy.request", job_id, f"devices={','.join(targets)}")
        await self._log(job_id, None, "info", f"requested by {user} for {', '.join(targets)}")
        if running:
            await self._log(job_id, None, "info",
                            f"queued: patches are computed once deploy #{running} finishes")
        else:
            self._spawn(self._prepare(job_id))
        return job_id

    async def request_draft(self, user: str, change_set_id: int) -> int:
        """A deploy of a change set: its patches come from the fabric's copy with the change set
        applied, approved for a window, and the change set is applied to NetBox at the window's start."""
        change_set = await self.drafts.get(change_set_id)
        if not change_set["steps"]:
            raise DeployError(f"change set {change_set_id} is empty")
        site = change_set["site"]
        targets = await self._targets(site, None)
        from orchestrator.drafts import DraftError
        try:   # from here on it cannot change: its patches are about to be shown and approved
            await self.drafts.set_status(change_set_id, "scheduled", ("open",))
        except DraftError as exc:
            raise DeployError(str(exc)) from exc
        async with self.sessions() as session:
            job = Job(kind="deploy", status="preparing", site=site, requested_by=user,
                      params={"change_set": change_set_id})
            job.steps = [DeployStep(device=d, position=i, status="pending") for i, d in enumerate(targets)]
            session.add(job)
            await session.commit()
            job_id = job.id
        await audit(self.sessions, user, "deploy.request", job_id,
                    f"change set {change_set_id}, devices={','.join(targets)}")
        await self._log(job_id, None, "info", f"requested by {user}: change set {change_set_id} "
                        f"({len(change_set['steps'])} change(s)) on {', '.join(targets)}")
        self._spawn(self._prepare_draft(job_id))
        return job_id

    async def _draft_patches(self, job: Job) -> tuple[dict[str, str], object]:
        """Each switch's patch with the job's change set applied to a fresh copy of NetBox, and the copy."""
        copy = await self.drafts.copy(job.params["change_set"], fresh=True)
        devices = [s.device for s in job.steps]
        configs = dict(zip(devices, await asyncio.gather(*(self.annet.current(d) for d in devices))))
        patches = await self.draft_patches(job.site, copy, configs)
        return {d: redact(patches.get(d, "")) for d in devices}, copy

    async def _prepare_draft(self, job_id: int) -> None:
        job = await self._job(job_id)
        try:
            patches, copy = await self._draft_patches(job)
            for step in job.steps:
                patch = patches[step.device]
                await self._set_step(step.id, status="diffed" if patch else "unchanged", patch=patch)
                await self._log(job_id, step.device, "info", patch_note(patch))
            changed = any(patches.values())
            if changed:
                await self._check(job_id, job.site, list(patches), copy)
                await self._set_job(job_id, status="diffed")
                await self._log(job_id, None, "info", "awaiting approval for a window")
            else:
                await self._set_job(job_id, status="unchanged", finished_at=now())
                await self._log(job_id, None, "info", "the change set changes no switch: nothing to deploy")
                await self._release(job_id)
        except Exception as exc:
            log.exception("preparing deploy job %s failed", job_id)
            await self._fail(job_id, redact(str(exc)))

    async def _release(self, job_id: int) -> None:
        """A change set whose job ended without applying it can be changed and deployed again."""
        job = await self._job(job_id)
        if self.drafts is None or not (job.params or {}).get("change_set"):
            return
        from orchestrator.drafts import DraftError
        try:
            await self.drafts.set_status(job.params["change_set"], "open", ("scheduled",))
            await self._log(job_id, None, "info", f"change set {job.params['change_set']} kept, open again")
        except DraftError:
            pass   # already applied (or discarded): nothing to give back

    async def _apply_draft(self, job: Job, steps: list[DeployStep]) -> str | None:
        """At a window's start: check the change set's patches on a fresh copy, then apply it to NetBox
        (a rollback point first, restored if applying or the real patches go wrong). Returns why not."""
        from orchestrator.drafts import DraftError
        change_set = job.params["change_set"]
        try:
            patches, _ = await self._draft_patches(job)
        except Exception as exc:
            return f"change set {change_set} cannot be applied now: {redact(str(exc))}; nothing was changed"
        if problem := await self._differing(job, steps, patches, draft=True):
            return f"{problem}; nothing was changed or deployed"
        point = await self.snapshots.take(job.site, job.id) if self.snapshots else None
        await self._log(job.id, None, "info", f"applying change set {change_set} to intent")
        try:
            await self.drafts.apply(change_set)
        except DraftError as exc:
            return await self._undo_draft(job, point, f"applying change set {change_set} stopped: {exc}")
        if problem := await self._recheck(job, [s for s in steps if s.status == "diffed"], None, ask=False):
            return await self._undo_draft(job, point, problem)
        await self.drafts.set_status(change_set, "applied", ("scheduled",))
        await audit(self.sessions, job.approved_by, "change-set.apply", job.id, f"{job.site} #{change_set}")
        return None

    async def _undo_draft(self, job: Job, point: dict | None, problem: str) -> str:
        from orchestrator import rollback
        if point is None:
            return f"{problem}; the change set stays applied (no rollback point)"
        try:
            planned = await rollback.preview(self.netbox, self.snapshots, job.site, point["id"], None)
            result = await rollback.apply(self.netbox, self.snapshots, job.site, point["id"], None,
                                          len(planned["operations"]))
            if result["failed"]:
                return f"{problem}; taking it back out stopped at {result['failed']['text']}: {result['failed']['error']}"
        except Exception as exc:
            return f"{problem}; taking it back out failed: {redact(str(exc))}"
        return f"{problem}; intent was put back as before the window"

    async def _drain(self) -> None:
        """Prepare queued jobs, oldest first, once no rollout is running."""
        async with self._lock:
            if await rolling_out(self.sessions) is not None:
                return
            async with self.sessions() as session:
                queued = (await session.scalars(
                    select(Job).where(Job.kind == "deploy", Job.status == "queued").order_by(Job.id)
                )).all()
                for job in queued:
                    job.status = "preparing"
                await session.commit()
                ids = [job.id for job in queued]
        for job_id in ids:
            await self._log(job_id, None, "info", "no rollout running any more: computing the patches")
            self._spawn(self._prepare(job_id))

    async def _sites(self, site: str | None, devices: list[str]) -> list[str]:
        if site:
            return [site]
        found = [await self.netbox.find_one("dcim/devices", name=d) for d in devices]
        return sorted({_slug((d or {}).get("site")) for d in found} - {None})

    async def _check(self, job_id: int, site: str | None, devices: list[str], copy=None) -> dict | None:
        """Run the pre-deploy checks of the job's fabrics and keep them with the job; on a
        change set's copy of NetBox for a change set's deploy."""
        if self.checker is None:
            return None
        results = []
        for fabric in await self._sites(site, devices):
            try:
                results.append(await (self.checker(fabric, copy) if copy is not None else self.checker(fabric)))
            except Exception as exc:   # unchecked is not approvable: approving again runs them again
                results.append({"fabric": fabric, "blocking": True, "findings": [{
                    "severity": "error", "check": "not-run", "objects": [],
                    "text": f"the checks could not run: {redact(str(exc))}"}]})
        checks = {"fabrics": results, "blocking": any(r["blocking"] for r in results), "at": now().isoformat()}
        async with self.sessions() as session:
            job = await session.get(Job, job_id)
            job.checks = checks
            await session.commit()
        findings = [f for r in results for f in r["findings"]]
        errors = sum(f["severity"] == "error" for f in findings)
        await self._log(job_id, None, "warn" if findings else "info",
                        f"pre-deploy checks: {errors} error(s), {len(findings) - errors} warning(s)"
                        + ("; errors must be fixed before approval" if errors else ""))
        return checks

    async def approve(self, job_id: int, user: str, window: tuple[datetime, datetime] | None = None) -> None:
        job = await self._job(job_id)
        if job is not None and job.kind == self.KIND and job.status == "reapproval":
            if (waiting := self._reapproved.get(job_id)) is None:
                raise DeployError(f"job {job_id} no longer waits for an approval")
            await self._set_job(job_id, params={**(job.params or {}), "reapproved_by": user})
            await audit(self.sessions, user, f"{self.KIND}.reapprove", job_id)
            await self._log(job_id, None, "info", f"new patch approved by {user}")
            waiting.set()
            return
        if window and (problem := window_problem(*window, self.clock())):
            raise DeployError(problem)
        # Pre-deploy checks again, now: intent may have changed since the job was prepared.
        job = await self._job(job_id)
        draft = job is not None and (job.params or {}).get("change_set")
        if draft and not window:
            raise DeployError(f"job {job_id} deploys a change set saved for a window: choose its window")
        if job is not None and job.kind == "deploy" and job.status == "diffed":
            copy = await self.drafts.copy(draft, fresh=True) if draft else None
            checks = await self._check(job_id, job.site, [s.device for s in job.steps if s.status == "diffed"], copy)
            if checks and checks["blocking"]:
                errors = [f["text"] for r in checks["fabrics"] for f in r["findings"] if f["severity"] == "error"]
                raise DeployError("pre-deploy checks found errors: " + "; ".join(errors))
        async with self._lock:
            async with self.sessions() as session:
                job = await session.get(Job, job_id)
                if job is None or job.kind != "deploy":
                    raise DeployError(f"no deploy job {job_id}")
                if job.status == "queued":
                    raise DeployError(f"job {job_id} is queued behind a running deploy; approve it once it is prepared")
                if job.status != "diffed":
                    raise DeployError(f"job {job_id} is {job.status}, not awaiting approval")
                # Deploys may have been turned off for a fabric since this one was prepared.
                changing = (await session.scalars(select(DeployStep.device).where(
                    DeployStep.job_id == job_id, DeployStep.status == "diffed"))).all()
                busy = await rolling_out(self.sessions)
                if busy is not None and not window:
                    raise DeployError(f"job {busy} is still rolling out; one rollout at a time")
                await self._targets(None, list(changing))   # refuses a fabric deploys are off for
                job.status, job.approved_by, job.approved_at = "scheduled" if window else "approved", user, now()
                job.window_start, job.window_end = window or (None, None)
                await session.commit()
        await self._approved(job_id, user, window)

    async def _approved(self, job_id: int, user: str, window: tuple[datetime, datetime] | None) -> None:
        if window:
            from orchestrator.windows import outside_usual, usual_windows

            span = f"{window[0].isoformat()} to {window[1].isoformat()}"
            job = await self._job(job_id)
            usual = usual_windows().get(job.site) if job.site else None
            outside = usual is not None and outside_usual(usual, *window)
            await audit(self.sessions, user, f"{self.KIND}.schedule", job_id,
                        f"window={span}" + (" (outside the fabric's usual window)" if outside else ""))
            await self._log(job_id, None, "info", f"approved by {user} for the window {span}")
            if outside:
                await self._log(job_id, None, "warn", f"this window is outside {job.site}'s usual window")
            await self.tick()   # a window already open starts now
            return
        await audit(self.sessions, user, f"{self.KIND}.approve", job_id)
        await self._log(job_id, None, "info", f"approved by {user}")
        self._spawn(self._rollout(job_id))

    # -- change windows --------------------------------------------------------------

    async def tick(self) -> None:
        """Start the scheduled jobs whose window is open; fail those whose window passed unstarted."""
        at = self.clock()
        started, ended, waiting, soon = [], [], [], []
        async with self._lock:
            busy = await rolling_out(self.sessions)
            async with self.sessions() as session:
                coming = (await session.scalars(select(Job).where(
                    Job.kind == self.KIND, Job.status == "scheduled", Job.window_start > at,
                    Job.window_start <= at + REMIND_BEFORE))).all()
                for job in coming:
                    if not (job.params or {}).get("reminded"):
                        job.params = {**(job.params or {}), "reminded": True}
                        soon.append(job)
                due = (await session.scalars(select(Job).where(
                    Job.kind == self.KIND, Job.status == "scheduled", Job.window_start <= at
                ).order_by(Job.window_start, Job.id))).all()
                for job in due:
                    if job.window_end <= at:
                        job.status, job.finished_at = "failed", now()
                        job.error = "the window ended before the job could start (platform down or another rollout)"
                        for step in job.steps:
                            if step.status == "diffed":
                                step.status = "skipped"
                        ended.append(job.id)
                    elif busy is None:
                        job.status, busy = "approved", job.id
                        started.append((job.id, at - job.window_start))
                    elif job.id not in self._waiting:
                        waiting.append((job.id, busy))
                await session.commit()
        for job in soon:
            minutes = round((job.window_start - at).total_seconds() / 60)
            await self._news(job, "starts soon", f"its window starts in {minutes} min ({job.window_start.isoformat()})")
        for job_id in ended:
            await self._news(await self._job(job_id), "failed", "the window ended before the job could start; nothing was sent")
            await self._log(job_id, None, "error", "the window ended before the job could start; nothing was sent")
            await audit(self.sessions, None, f"{self.KIND}.failed", job_id, "window ended before its start")
            await self._release(job_id)
        for job_id, other in waiting:
            self._waiting.add(job_id)
            await self._log(job_id, None, "warn", f"window open, waiting for job #{other} to finish its rollout")
        for job_id, late in started:
            self._waiting.discard(job_id)
            await self._news(await self._job(job_id), "started", "the window opened", alert=False)
            await self._log(job_id, None, "info", "window opened" + (
                f" ({int(late.total_seconds() // 60)} min late: the platform was down or busy at its start)"
                if late > timedelta(minutes=1) else ""))
            self._spawn(self._rollout(job_id))

    async def cancel(self, job_id: int, user: str) -> None:
        """A scheduled job ends now; a rolling-out one finishes its running wave and starts no other."""
        async with self._lock:
            async with self.sessions() as session:
                job = await session.get(Job, job_id)
                if job is None or job.kind != self.KIND:
                    raise DeployError(f"no {self.KIND} job {job_id}")
                if job.status == "scheduled":
                    job.status, job.finished_at, job.error = "cancelled", now(), f"cancelled by {user} before its window"
                    for step in job.steps:
                        if step.status == "diffed":
                            step.status = "skipped"
                    running = False
                elif job.status in ROLLING_OUT:
                    self._cancelled[job_id] = user
                    if (waiting := self._reapproved.get(job_id)) is not None:
                        waiting.set()
                    running = True
                else:
                    raise DeployError(f"job {job_id} is {job.status}: nothing to cancel")
                await session.commit()
        await audit(self.sessions, user, f"{self.KIND}.cancel", job_id)
        if not running:
            await self._news(await self._job(job_id), "cancelled", f"cancelled by {user} before its window; nothing was sent")
            await self._release(job_id)
        await self._log(job_id, None, "warn", f"cancelled by {user}: the running "
                        f"{'wave' if self.KIND == 'deploy' else 'switch'} finishes, nothing else starts"
                        if running else f"cancelled by {user}; nothing was sent")

    def _stop_reason(self, job: Job) -> tuple[str, str] | None:
        """Why no further wave may start: (job status, reason)."""
        if (user := self._cancelled.get(job.id)) is not None:
            return "cancelled", f"cancelled by {user}"
        if job.window_end is not None and self.clock() >= job.window_end:
            return "stopped", "the window ended"
        return None

    async def _stop(self, job: Job, stop: tuple[str, str], rest: list[DeployStep]) -> None:
        status, reason = stop
        for step in rest:
            await self._set_step(step.id, status="skipped")
            await self._log(job.id, step.device, "warn", f"not started: {reason}")
        self._cancelled.pop(job.id, None)
        await self._set_job(job.id, status=status, finished_at=now(), error=f"{reason}; "
                            f"{len(rest)} switch(es) not started")
        await self._news(job, status, f"{reason}; {len(rest)} switch(es) not started")
        await audit(self.sessions, job.approved_by, f"{self.KIND}.{status}", job.id, reason)

    async def _recheck(self, job: Job, steps: list[DeployStep], generators: list[str] | None,
                       ask: bool = True) -> str | None:
        """At a window's start, before any switch changes: every patch must still be the approved one
        (else an approver is asked, `_differing`), and the pre-deploy checks have no error. Returns why not."""
        await self._log(job.id, None, "info", "checking every switch against the approved patches")
        current = await asyncio.gather(*(self.annet.patch(s.device, generators) for s in steps))
        current = {s.device: redact(patch) for s, patch in zip(steps, current)}
        differ = [s.device for s in steps if current[s.device] != (s.patch or "")]
        if differ and not ask:
            return f"the patch of {', '.join(differ)} is not the one approved"
        if differ and (problem := await self._differing(job, steps, current, draft=False)):
            return f"{problem}; nothing was deployed"
        checks = await self._check(job.id, job.site, [s.device for s in steps])
        if checks and checks["blocking"]:
            errors = [f["text"] for r in checks["fabrics"] for f in r["findings"] if f["severity"] == "error"]
            return "pre-deploy checks found errors at the window's start: " + "; ".join(errors)
        return None

    async def _differing(self, job: Job, steps: list[DeployStep], new: dict[str, str], draft: bool) -> str | None:
        """At a window's start every switch's patch is `new`. None: go on, the steps taking `new`; else why not.
        No switch is ever skipped: a change set's difference goes on by itself when it
        is safe, any other difference only once an approver approves the new patch."""
        differ = [s for s in steps if new.get(s.device, "") != (s.patch or "")]
        if not differ:
            return None
        names = ", ".join(s.device for s in differ)
        reason = None if draft else "the approved patches were made from live intent, which changed since"
        for step in differ if draft else []:
            if reason := await self._unsafe(job, step, new.get(step.device, "")):
                break
        if reason is None:
            await self._log(job.id, None, "warn", f"the patch of {names} changed since it was approved, only by "
                            "additions of the approved kinds after a verified deploy: going on with the new patch")
        elif problem := await self._ask(job, names, new, reason):
            return problem
        for step in differ:
            step.patch = new.get(step.device, "")
            step.status = "diffed" if step.patch else "unchanged"
            await self._set_step(step.id, patch=step.patch, status=step.status)
        return None

    async def _unsafe(self, job: Job, step: DeployStep, new: str) -> str | None:
        """Why this switch's new patch may not go on by itself (orchestrator/windows.py for the lines)."""
        from orchestrator.windows import safe_difference

        if reason := safe_difference(step.patch or "", new, await self._guarded_ports(step.device)):
            return f"{step.device}: {reason}"
        async with self.sessions() as session:
            verified = await session.scalar(select(DeployStep.id).join(Job, DeployStep.job_id == Job.id).where(
                DeployStep.device == step.device, DeployStep.status == "verified",
                Job.finished_at > job.approved_at).limit(1))
        if verified is None:
            return f"{step.device}: no deploy of it was verified since the approval (a hand change, or a change not deployed)"
        if await self.annet.patch(step.device):
            return f"{step.device}: it has saved changes that are not deployed yet"
        return None

    async def _guarded_ports(self, device: str) -> set[str]:
        """Fabric links (cabled ports and their Eth-Trunks), the peer-link and unnumbered links."""
        found = await self.netbox.find_one("dcim/devices", name=device)
        interfaces = await self.netbox.find("dcim/interfaces", device_id=found["id"]) if found else []
        guarded = {i["name"] for i in interfaces if i.get("cable") or
                   {t.get("slug") for t in i.get("tags") or []} & {"peer-link", "unnumbered"}}
        return guarded | {(i.get("lag") or {}).get("name") for i in interfaces if i["name"] in guarded} - {None}

    async def _news(self, job: Job, state: str, text: str, alert: bool = True) -> None:
        """A window job's start, end or failure: on the timeline, and to the alert receiver
        (starts in 15 min, finished, failed or refused; not started)."""
        if job is None or job.window_start is None:
            return
        from orchestrator import alerting

        what = f"{self.KIND.capitalize()} #{job.id} of {job.site or 'several fabrics'}"
        if self.events is not None:
            try:
                await self.events.record([{"site": job.site, "device": None, "kind": "window", "subject": what,
                                           "state": state, "previous": None, "detail": redact(text),
                                           "severity": "problem" if state in ("failed", "stopped") else "change"}])
            except Exception as exc:
                log.warning("window event of job %s not recorded: %s", job.id, exc)
        if alert:
            try:
                await alerting.send("FabricChangeWindow", "warning" if state in ("failed", "stopped") else "info",
                                    f"{what}: {state}: {redact(text)}",
                                    {"fabric": job.site or "", "job": str(job.id), "state": state})
            except Exception as exc:
                await self._log(job.id, None, "warn", f"the alert receiver could not be told: {redact(str(exc))}")

    async def _ask(self, job: Job, names: str, new: dict[str, str], reason: str) -> str | None:
        """Wait for an approver to approve the new patches (`approve` again); None once one did."""
        from orchestrator import alerting

        event = self._reapproved[job.id] = asyncio.Event()
        minutes = round(self.reapprove_timeout / 60)
        await self._set_job(job.id, status="reapproval", params={**(job.params or {}), "new_patches": new, "asked": reason})
        await self._log(job.id, None, "warn", f"the patch of {names} changed since it was approved: {reason}; "
                        f"an approver may approve the new patch within {minutes} min")
        await audit(self.sessions, None, f"{self.KIND}.reapproval", job.id, reason)
        try:
            await alerting.send("FabricWindowNeedsApproval", "warning",
                                f"Deploy #{job.id} of {job.site}: its window started and the patch of {names} changed "
                                f"({reason}). Approve the new patch within {minutes} min or nothing is deployed.",
                                {"fabric": job.site or ""}, minutes=minutes)
        except Exception as exc:
            await self._log(job.id, None, "warn", f"the alert receiver could not be told: {redact(str(exc))}")
        try:
            await asyncio.wait_for(event.wait(), self.reapprove_timeout)
        except asyncio.TimeoutError:
            return f"the patch of {names} changed since it was approved ({reason}) and no approver approved it within {minutes} min"
        finally:
            self._reapproved.pop(job.id, None)
            await self._set_job(job.id, status="deploying")
        if (user := self._cancelled.get(job.id)) is not None:
            return f"cancelled by {user} while waiting for an approver"
        return None

    # -- background work ----------------------------------------------------------------

    async def _prepare(self, job_id: int) -> None:
        job = await self._job(job_id)
        generators = job.generators.split(",") if job.generators else None
        try:
            # Every import before any patch (an M-LAG pair's import changes both leaves' intent),
            # each phase on every switch at once.
            if self.importer is not None:
                # One import per intent target: a pair's import reads both leaves.
                found = await asyncio.gather(*(self.importer.store.target_of(s.device) for s in job.steps))
                once = {target or step.device: step.device for step, (_, target, _) in zip(job.steps, found)}
                await asyncio.gather(*(self._import(job_id, device) for device in once.values()))
            results = await asyncio.gather(*(self._patch_one(job_id, step, generators) for step in job.steps),
                                           return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            changed = sum(results)
            status = "diffed" if changed else "unchanged"
            if changed:
                await self._check(job_id, job.site, [s.device for s in job.steps])
            await self._set_job(job_id, status=status, finished_at=None if changed else now())
            await self._log(job_id, None, "info", "awaiting approval" if changed else "nothing to deploy")
        except Exception as exc:
            log.exception("preparing deploy job %s failed", job_id)
            await self._fail(job_id, redact(str(exc)))

    async def _import(self, job_id: int, device: str) -> None:
        report = await self.importer.run(device, user=f"deploy #{job_id}")
        if report.imported:
            await self._log(job_id, device, "info",
                            f"imported hand-made routing objects into intent: {', '.join(report.imported)}")
        if report.error:
            await self._log(job_id, device, "warn", f"routing import skipped: {report.error}")

    async def _patch_one(self, job_id: int, step: DeployStep, generators: list[str] | None) -> bool:
        await self._log(job_id, step.device, "info", "computing the patch")
        try:
            patch = redact(await self.annet.patch(step.device, generators))
        except AnnetError as exc:
            await self._set_step(step.id, status="failed", error=redact(str(exc)), finished_at=now())
            raise DeployError(f"{step.device}: cannot compute the patch") from exc
        await self._set_step(step.id, status="diffed" if patch else "unchanged", patch=patch)
        await self._log(job_id, step.device, "info", patch_note(patch))
        return bool(patch)

    async def _rollout(self, job_id: int) -> None:
        try:
            await self._roll(job_id)
        finally:
            self._cancelled.pop(job_id, None)
            await self._drain()


    async def _roll(self, job_id: int) -> None:
        job = await self._job(job_id)
        generators = job.generators.split(",") if job.generators else None
        await self._set_job(job_id, status="deploying")
        if job.window_start is not None:   # every switch, unchanged ones included, may differ by now
            if problem := await (self._apply_draft(job, job.steps) if (job.params or {}).get("change_set")
                                 else self._recheck(job, job.steps, generators)):
                for step in job.steps:
                    if step.status == "diffed":
                        await self._set_step(step.id, status="skipped")
                await self._fail(job_id, problem)
                return
        steps = [s for s in job.steps if s.status == "diffed"]
        by_device = {s.device: s for s in steps}
        waves = rollout_waves(list(by_device), await self.netbox.find("dcim/devices", status="active"))
        if any(touches_login(s.patch) for s in steps):   # AAA changes one switch at a time
            waves = [[device] for wave in waves for device in wave]
        # A verified switch is saved and backed up while the next wave deploys;
        # the job ends only once every one of them is.
        tails: list[asyncio.Task] = []
        failure = stopped = None
        try:
            for index, wave in enumerate(waves):
                if stopped := self._stop_reason(job):
                    break
                if len(waves) > 1:
                    await self._log(job_id, None, "info", f"wave {index + 1} of {len(waves)}: {', '.join(wave)}")
                # A failure starts no new wave; the rest of this one finish and are verified.
                results = await asyncio.gather(*(self._deploy_one(job_id, by_device[d], generators) for d in wave),
                                               return_exceptions=True)
                failed = []
                for device, result in zip(wave, results):
                    if isinstance(result, BaseException):
                        reason = redact(str(result))
                        await self._set_step(by_device[device].id, status="failed", error=reason, finished_at=now())
                        await self._log(job_id, device, "error", reason)
                        failed.append(f"{device}: {reason.partition(chr(10))[0]}")
                    else:
                        tails.append(asyncio.create_task(self._save_and_backup(job_id, device)))
                if failed:
                    for later in (d for w in waves[index + 1:] for d in w):
                        await self._set_step(by_device[later].id, status="skipped")
                        await self._log(job_id, later, "warn", "skipped: a device of an earlier wave failed")
                    failure = "; ".join(failed)
                    break
        finally:
            await asyncio.gather(*tails)
        if failure:
            await self._fail(job_id, failure)
            return
        if stopped:
            await self._stop(job, stopped, [by_device[d] for w in waves[index:] for d in w])
            return
        await self._set_job(job_id, status="verified", finished_at=now())
        await self._news(job, "verified", f"all {len(steps)} switch(es) deployed and verified")
        await audit(self.sessions, job.approved_by, "deploy.verified", job_id)
        await self._log(job_id, None, "info", "all devices deployed and verified")
        await self._snapshot(job_id, job.site, [s.device for s in steps])

    async def _snapshot(self, job_id: int, site: str | None, devices: list[str]) -> None:
        """Keep the intent of each fabric this deploy verified: a rollback point."""
        if self.snapshots is None:
            return
        try:
            sites = {site} if site else {_slug((await self.netbox.find_one("dcim/devices", name=d) or {}).get("site"))
                                         for d in devices} - {None}
            for fabric in sorted(sites):
                kept = await self.snapshots.take(fabric, job_id)
                await self._log(job_id, None, "info", f"intent of {fabric} kept as rollback point #{kept['id']}")
        except Exception as exc:   # the deploy is verified; only the rollback point is missing
            await self._log(job_id, None, "warn", f"intent snapshot failed: {redact(str(exc))}")

    async def _deploy_one(self, job_id: int, step: DeployStep, generators: list[str] | None) -> None:
        device = step.device
        await self._set_step(step.id, status="deploying", started_at=now())
        current = redact(await self.annet.patch(device, generators))
        if current != step.patch:
            raise DeployError("the patch changed since it was approved; request a new deploy")
        links_before = await self.verifier.links_up(device)
        await self._log(job_id, device, "info", f"deploying ({len(current.splitlines())} lines)")
        if needs_weak_algorithms(step.patch):
            await self._weak_algorithms(job_id, device)
        if touches_login(step.patch):
            await self._deploy_login(job_id, device, generators)
        else:
            await self.annet.deploy(device, generators)
        deployed_at = time.time()
        await self._set_step(step.id, status="verifying")
        await self._log(job_id, device, "info", "deployed; verifying (diff, collector, links)")
        await self.verifier.verify(device, links_before, deployed_at)
        await self._record_in_sync(job_id, device)
        async with self._writing:   # switches of one wave change the same fabric documents
            await self._clear_retired(job_id, device)
        await self._set_step(step.id, status="verified", finished_at=now())
        await self._log(job_id, device, "info", "verified")

    async def _deploy_login(self, job_id: int, device: str, generators: list[str] | None) -> None:
        """AAA reaches a switch only under `commit trial`. Annet always ends in a plain `commit`,
        so it sends the rest and the generator's own patch goes through switch_cli, confirmed only
        after fresh SSH and NETCONF logins. Only the session that started a trial can abort it: if
        that session is lost the job waits for the switch's timer before saying it failed."""
        from orchestrator import switch_cli

        await self.annet.deploy(device, generators, excluded=LOGIN_GENERATOR)
        patch = await self.annet.patch(device, [LOGIN_GENERATOR])
        if not patch:   # nothing of the generator's (only its name appeared in an approved line)
            return
        found = await self.netbox.find_one("dcim/devices", name=device)
        ip = ((found or {}).get("primary_ip4") or {}).get("address", "").split("/")[0]
        if not ip:
            raise DeployError("no management address")
        trial_open = False

        async def logins() -> str | None:
            nonlocal trial_open
            trial_open = True
            await self._log(job_id, device, "info", "under a trial commit; logging in again over SSH and NETCONF")
            try:
                await switch_cli.run(device, ip, [switch_cli.DISPLAY_VERSION])
                await self.reader.has_module(device, "huawei-aaa")
            except Exception as exc:
                return f"the trial was taken back: the platform could not log in again ({redact(str(exc))[:300]})"
            return None

        try:
            await self.annet.trial(device, ip, patch, logins, tuple(EXTRA))
        except switch_cli.SwitchCliError as exc:
            if trial_open and "taken back" not in str(exc):
                await asyncio.sleep(switch_cli.TRIAL_SECONDS + 15)
                raise DeployError(f"the session was lost during a trial commit, the switch's timer ended it: {exc}") from exc
            raise DeployError(str(exc)) from exc
        await self._log(job_id, device, "info", "switch login confirmed after both logins")

    async def _weak_algorithms(self, job_id: int, device: str) -> None:
        """Install WEAKEA before a patch that turns SNMPv2c on (shown with the patch at prepare)."""
        from orchestrator import switch_cli

        found = await self.netbox.find_one("dcim/devices", name=device)
        ip = ((found or {}).get("primary_ip4") or {}).get("address", "").split("/")[0]
        if not ip:
            raise DeployError("no management address")
        try:
            [output] = await self.annet.commands(device, ip, [switch_cli.WEAKEA])
        except switch_cli.SwitchCliError as exc:
            if "Unrecognized command" in str(exc):   # a release without the package: v2c is built in
                await self._log(job_id, device, "info", "no WEAKEA package on this release; not needed")
                return
            raise DeployError(f"WEAKEA could not be installed: {redact(str(exc))}") from exc
        await self._log(job_id, device, "info", "WEAKEA already installed" if "skipped" in output else "WEAKEA installed")

    async def _save_and_backup(self, job_id: int, device: str) -> None:
        await self._save(job_id, device)
        await self._backup(job_id, device)

    async def _save(self, job_id: int, device: str) -> None:
        """`save` the verified config, so the switch boots into it (deploys did not save, so a
        reboot or power loss went back to the last hand-saved config). A failure is a warning: the
        deploy is done; the next deploy or upgrade saves again."""
        from orchestrator.switch_cli import SAVE

        try:
            found = await self.netbox.find_one("dcim/devices", name=device)
            ip = ((found or {}).get("primary_ip4") or {}).get("address", "").split("/")[0]
            if not ip:
                raise DeployError("no management address")
            await self.annet.commands(device, ip, [SAVE])
            await self._log(job_id, device, "info", "config saved")
        except Exception as exc:
            await self._log(job_id, device, "warn", f"config not saved (a reboot would boot the last saved "
                                                    f"config): {redact(str(exc))}")

    async def _backup(self, job_id: int, device: str) -> None:
        """Keep the verified switch's running config. A failure is a warning: the deploy is done."""
        if self.backups is None or self.backups.problem() is not None:
            return
        try:
            version = await self.backups.capture(device, "deploy", job_id=job_id)
            await self._log(job_id, device, "info", f"config backed up (version #{version['id']})"
                            if version["new"] else "config unchanged since its last backup")
        except Exception as exc:
            await self._log(job_id, device, "warn", f"config backup failed: {redact(str(exc))}")

    # -- storage helpers -----------------------------------------------------------------

    async def _clear_retired(self, job_id: int, device: str) -> None:
        """Routing objects retired from intent are gone once the device's diff is empty."""
        from orchestrator.routing_intent import RoutingStore

        try:
            await RoutingStore(self.netbox).clear_retired(device)
        except Exception as exc:   # the deploy is verified; a stale retired entry only lingers
            await self._log(job_id, device, "warn", f"could not clear retired routing objects: {exc}")
        try:   # what a DC link no longer has
            from orchestrator import dci

            await dci.verified(self.netbox, device)
        except Exception as exc:
            await self._log(job_id, device, "warn", f"could not clear retired DCI lines: {exc}")

    async def _record_in_sync(self, job_id: int, device: str) -> None:
        """Verification found the device's full diff empty: keep that as its latest diff,
        so drift clears now instead of at the next drift run."""
        async with self.sessions() as session:
            stamp = now()
            session.add(DiffSnapshot(job_id=job_id, device=device, status="ok", has_changes=False, diff="",
                                     created_at=stamp, finished_at=stamp))
            await session.commit()

    async def _job(self, job_id: int) -> Job:
        async with self.sessions() as session:
            return await session.get(Job, job_id)

    async def _set_job(self, job_id: int, **values) -> None:
        async with self.sessions() as session:
            await session.execute(update(Job).where(Job.id == job_id).values(**values))
            await session.commit()

    async def _set_step(self, step_id: int, **values) -> None:
        async with self.sessions() as session:
            await session.execute(update(DeployStep).where(DeployStep.id == step_id).values(**values))
            await session.commit()

    async def _fail(self, job_id: int, reason: str) -> None:
        await self._set_job(job_id, status="failed", finished_at=now(), error=reason)
        job = await self._job(job_id)
        await audit(self.sessions, job.approved_by or job.requested_by, f"{self.KIND}.failed", job_id, reason)
        await self._log(job_id, None, "error", f"job failed: {reason}")
        await self._news(job, "failed", reason)
        await self._release(job_id)

    async def _log(self, job_id: int, device: str | None, level: str, message: str) -> None:
        async with self.sessions() as session:
            session.add(JobLog(job_id=job_id, device=device, level=level, message=redact(message)))
            await session.commit()


class WindowScheduler:
    """Starts change windows: every WINDOW_TICK seconds, each job runner's scheduled jobs."""

    def __init__(self, *runners: DeployJobs, interval: float = WINDOW_TICK):
        self.runners = runners
        self.interval = interval
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            for runner in self.runners:
                try:
                    await runner.tick()
                except Exception:   # NetBox or the database away for a moment: the next tick tries again
                    log.exception("change window tick failed")
            await asyncio.sleep(self.interval)

def deploy_json(job: Job, log_rows: list[JobLog] | None = None) -> dict:
    body = {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "site": job.site,
        "requested_by": job.requested_by,
        "approved_by": job.approved_by,
        "approved_at": job.approved_at.isoformat() if job.approved_at else None,
        "generators": job.generators.split(",") if job.generators else None,
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error": job.error,
        "window_start": job.window_start.isoformat() if job.window_start else None,
        "window_end": job.window_end.isoformat() if job.window_end else None,
        "checks": job.checks,
        "params": job.params,
        "steps": [
            {
                "device": s.device,
                "status": s.status,
                "patch": s.patch,
                "error": s.error,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            }
            for s in job.steps
        ],
    }
    if log_rows is not None:
        body["log"] = [
            {"at": r.at.isoformat(), "device": r.device, "level": r.level, "message": r.message} for r in log_rows
        ]
    return body


async def list_deploys(sessions: async_sessionmaker, limit: int, kind: str = "deploy") -> list[dict]:
    async with sessions() as session:
        jobs = (await session.scalars(
            select(Job).where(Job.kind == kind).order_by(Job.id.desc()).limit(limit)
        )).all()
        return [deploy_json(j) for j in jobs]


async def get_deploy(sessions: async_sessionmaker, job_id: int, kind: str = "deploy") -> dict | None:
    async with sessions() as session:
        job = await session.get(Job, job_id)
        if job is None or job.kind != kind:
            return None
        rows = (await session.scalars(select(JobLog).where(JobLog.job_id == job_id).order_by(JobLog.id))).all()
        return deploy_json(job, list(rows))
