"""Switch software upgrades: a job like a deploy, with a plan per switch
shown for approval instead of a patch.

States of a job: preparing -> diffed (awaiting approval) -> approved -> deploying -> verified; failed;
unchanged when every switch already runs what was asked.
States of a switch: pending -> diffed | unchanged -> upgrading -> rebooting -> verifying -> verified |
failed; skipped when an earlier switch failed.

Per switch, in order, the next one only once the previous one is verified: the plan read again and
compared with the approved one; its diff must be empty (the reboot saves the running config); copies
already on flash digest-checked; old images deleted (`.cc` and `.PAT` neither running nor next
startup); each image downloaded from the platform's image server with a one-time account (removed
right after) and digest-checked; next startup set and confirmed; the M-LAG peer up and healthy;
reboot; back on SSH; verified (the release and patch asked, empty diff, collector polling, links
back). An upgrade rolls out like a deploy: intent is frozen and deploys queue meanwhile.
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass

from sqlalchemy import select

from orchestrator import switch_cli
from orchestrator.auth import audit
from orchestrator.db import DeployStep, Job, now
from orchestrator.deploys import ACTIVE, DeployError, DeployJobs, VerificationFailed, rolling_out, window_problem
from orchestrator.images import ImageStore
from orchestrator.redact import redact
from orchestrator.routing_intent import status_of
from orchestrator.segments import _cf, _ref_id
from orchestrator.sftp import Accounts, progress

log = logging.getLogger(__name__)

IMAGE = re.compile(r"\.(cc|pat)$", re.I)   # what counts as an image on flash: system software and patches


class UpgradeError(DeployError):
    pass


@dataclass
class UpgradeSettings:
    down_timeout: float = 600.0     # the switch stops answering after `reboot`
    up_timeout: float = 1800.0      # and answers SSH again (the test switch: 192 s without ping, SSH a minute later)
    verify_timeout: float = 600.0   # links and the collector after it is back
    poll: float = 10.0
    peer_max_age: float = 60.0      # the M-LAG peer's last poll
    flash_margin: int = 64 << 20    # shortcut: a fixed reserve; Huawei states no minimum free flash


def _mb(size: int) -> str:
    return f"{size / 1e6:.1f} MB"


def _matches(patch_release: str, system_release: str) -> bool:
    """V300R025SPH120 belongs to V300R025C00SPC500."""
    return system_release.startswith(patch_release.split("SPH")[0])


def server() -> tuple[str, int]:
    host = (os.environ.get("SFTP_ADDRESS") or "").strip()
    if not host:
        raise UpgradeError("set the image server address (Settings › Switch software) first: the switches "
                           "download from it")
    return host, int(os.environ.get("SFTP_PORT") or 2222)


@dataclass
class Plan:
    header: str
    release: str                        # running: which check its software has (switch_cli.verify)
    checks: list[tuple[str, str]]       # copies already on flash: (name, sha256)
    deletes: list[tuple[str, int]]
    downloads: list[tuple[str, int, str]]   # (name, size, sha256)
    startups: list[tuple[str, str]]     # (kind, name)
    free_after: int
    total_free: int

    def commands(self) -> list[str]:
        """What is approved and compared before the rollout (the free space moves with logs: not in it)."""
        host, port = server()
        sized = not switch_cli.has_digest(self.release)   # R023: the exact size before its package check
        return ([self.verify(n, h).command for n, h in self.checks]
                + [f"delete /unreserved flash:/{n}" for n, _ in self.deletes]
                + [line for n, _, h in self.downloads for line in (
                    "system-view",
                    f"sftp client-transfile get host-ip {host} {port} -vpn-instance _management_vpn_ "
                    f"username <one-time> password <one-time> sourcefile {n} destination flash:/{n}",
                    "return", *([switch_cli.size_of(n).command] if sized else []), self.verify(n, h).command)]
                + [switch_cli.startup(k, n).command for k, n in self.startups]
                + ["reboot (save the configuration: Y, reboot: Y)"])

    def verify(self, name: str, sha256: str) -> switch_cli.Step:
        kind = next(k for k, n in self.startups if n == name)
        return switch_cli.verify(name, sha256, kind, self.release)

    def text(self) -> str:
        sizes = {n: s for n, s in self.deletes} | {n: s for n, s, _ in self.downloads}
        lines = [f"{c}   # {_mb(sizes[n])}" if (n := c.rsplit("flash:/", 1)[-1]) in sizes and
                 c.startswith(("delete", "sftp")) else c for c in self.commands()]
        return "\n".join([f"# {self.header}", *lines,
                          f"# free flash after the downloads: {_mb(self.free_after)} (now {_mb(self.total_free)})"])


def approved_commands(text: str | None) -> list[str]:
    return [line.split("   # ")[0] for line in (text or "").splitlines() if not line.startswith("#")]


class UpgradeJobs(DeployJobs):
    KIND = "upgrade"

    def __init__(self, deploys: DeployJobs, images: ImageStore, accounts: Accounts | None = None,
                 settings: UpgradeSettings | None = None):
        super().__init__(deploys.sessions, deploys.annet, deploys.netbox, deploys.verifier)
        self.deploys = deploys
        self._lock = deploys._lock   # one approval at a time across deploys and upgrades
        self.images = images
        self.accounts = accounts or Accounts()
        self.settings = settings or UpgradeSettings()
        self._downloading: dict[int, dict] = {}   # job -> the download running now (memory only)

    def progress(self, job_id: int) -> dict | None:
        """The running download of a job, as far as the image server has sent it (the switch
        itself prints nothing until the file is complete)."""
        if (running := self._downloading.get(job_id)) is None:
            return None
        sent = progress(running["login"]) or {}
        elapsed = time.time() - running["started"]
        done = int(sent.get("bytes", 0))
        return {"device": running["device"], "file": running["file"], "size": running["size"], "bytes": done,
                "started": running["started"], "rate": done / elapsed if elapsed > 0 and done else None}

    async def recover(self) -> None:
        """An upgrade interrupted by a restart cannot resume: the switch may be mid-download or rebooting."""
        await self._reschedule_unstarted()
        async with self.sessions() as session:
            for job in (await session.scalars(select(Job).where(Job.kind == "upgrade", Job.status.in_(ACTIVE)))).all():
                for step in job.steps:
                    if step.status in ("upgrading", "rebooting", "verifying"):
                        step.status, step.finished_at = "failed", now()
                        step.error = ("orchestrator restarted during the upgrade: check the switch's version, "
                                      "next startup files and flash by hand")
                    elif step.status in ("pending", "diffed"):
                        step.status = "skipped"
                job.status, job.finished_at, job.error = "failed", now(), "orchestrator restarted during the job"
            await session.commit()

    # -- request and plan -------------------------------------------------------------------------

    async def _image(self, image_id: int | None, kind: str):
        if image_id is None:
            return None
        image = await self.images.get(image_id)
        if image is None or image.kind != kind:
            raise UpgradeError(f"no {kind} image {image_id} in the store")
        if not self.images.path(image).is_file():   # HA: each server has its own store
            raise UpgradeError(f"{image.name} is not on this server's store: upload it again")
        return image

    async def _images(self, params: dict):
        system = await self._image(params.get("system"), "system")
        patch = await self._image(params.get("patch"), "patch")
        if system is None and patch is None:
            raise UpgradeError("choose a system software image, a patch, or both")
        if system and patch and not _matches(patch.release, system.release):
            raise UpgradeError(f"patch {patch.release} is not for {system.release}")
        return system, patch

    async def request(self, user: str, devices: list[str], system: int | None = None,
                      patch: int | None = None) -> int:
        if not devices:
            raise UpgradeError("choose the switches to upgrade")
        server()
        params = {"system": system, "patch": patch}
        await self._images(params)
        targets = await self._targets(None, devices)
        sites = await self._sites(None, targets)
        if len(sites) != 1:   # approval rights are per fabric
            raise UpgradeError("one upgrade job per fabric: these switches are in " + ", ".join(sites))
        async with self.sessions() as session:
            job = Job(kind="upgrade", status="preparing", site=sites[0], requested_by=user, params=params)
            job.steps = [DeployStep(device=d, position=i, status="pending") for i, d in enumerate(targets)]
            session.add(job)
            await session.commit()
            job_id = job.id
        await audit(self.sessions, user, "upgrade.request", job_id, f"devices={','.join(targets)}")
        await self._log(job_id, None, "info", f"requested by {user} for {', '.join(targets)}")
        self._spawn(self._prepare(job_id))
        return job_id

    async def _address(self, device: str) -> str:
        found = await self.netbox.find_one("dcim/devices", name=device)
        ip = ((found or {}).get("primary_ip4") or {}).get("address", "").split("/")[0]
        if not ip:
            raise UpgradeError(f"{device} has no management address")
        return ip

    async def plan(self, device: str, ip: str, system, patch) -> Plan | None:
        """Read the switch (display only) and work out what the upgrade does there. None: it already
        runs what was asked."""
        out_version, out_startup, out_dir = await self.annet.commands(
            device, ip, [switch_cli.DISPLAY_VERSION, switch_cli.DISPLAY_STARTUP, switch_cli.DIR])
        release, running_patch = switch_cli.version(out_version)
        boot = switch_cli.startup_files(out_startup)
        files, free = switch_cli.flash(out_dir)
        if release is None or "next system" not in boot:
            raise UpgradeError("could not read the running release or the startup files")
        if (system is None or release == system.release) and (patch is None or running_patch == patch.release) \
                and (system is None or boot.get("next system") == system.name) \
                and (patch is None or boot.get("next patch") == patch.name):
            return None
        if system is None and not _matches(patch.release, release):
            raise UpgradeError(f"patch {patch.release} is not for the running {release}: upgrade the system "
                               "software with it")
        if system is None and boot.get("next system") != boot.get("system"):
            raise UpgradeError(f"the next startup system software is {boot.get('next system')}, not the running "
                               "one: set by hand, sort it out first")
        if patch is None and (running_patch or boot.get("next patch")):
            raise UpgradeError(f"the switch has patch {running_patch or boot.get('next patch')}: choose the patch "
                               f"for {system.release} too")
        keep = {boot.get(k) for k in ("system", "patch", "next system", "next patch")} - {None}
        wanted = [i for i in (system, patch) if i is not None]
        checks, deletes, downloads = [], [], []
        for image in wanted:
            if image.name not in files:
                downloads.append((image.name, image.size, image.sha256))
            elif files[image.name] == image.size:
                checks.append((image.name, image.sha256))
            elif image.name in keep:
                raise UpgradeError(f"flash:/{image.name} is in use and differs from the store's ({files[image.name]} "
                                   f"bytes, not {image.size})")
            else:
                deletes.append((image.name, files[image.name]))
                downloads.append((image.name, image.size, image.sha256))
        names = {i.name for i in wanted}
        deletes += [(n, s) for n, s in sorted(files.items()) if IMAGE.search(n) and n not in keep | names]
        total = free + sum(s for _, s in deletes)
        free_after = total - sum(s for _, s, _ in downloads)
        if free_after < self.settings.flash_margin:
            raise UpgradeError(f"not enough flash: {_mb(free_after)} would be left after the downloads "
                               f"(at least {_mb(self.settings.flash_margin)})")
        startups = [(i.kind, i.name) for i in wanted]
        header = (f"runs {release}" + (f" with patch {running_patch}" if running_patch else "")
                  + f"; next startup {boot.get('next system')}" + (f", {boot['next patch']}" if boot.get("next patch") else ""))
        return Plan(header, release, checks, deletes, downloads, startups, free_after, free)

    async def _prepare(self, job_id: int) -> None:
        job = await self._job(job_id)
        try:
            system, patch = await self._images(job.params)
            changed = 0
            for step in job.steps:
                await self._log(job_id, step.device, "info", "reading version, startup files and flash")
                try:
                    plan = await self.plan(step.device, await self._address(step.device), system, patch)
                except Exception as exc:
                    await self._set_step(step.id, status="failed", error=redact(str(exc)), finished_at=now())
                    raise UpgradeError(f"{step.device}: {redact(str(exc))}") from exc
                await self._set_step(step.id, status="diffed" if plan else "unchanged", patch=plan.text() if plan else "")
                changed += plan is not None
                files = " and ".join(i.name for i in (system, patch) if i)
                await self._log(job_id, step.device, "info", plan.header if plan else
                                f"already runs {files}, also its next startup: nothing to do (same release "
                                "again only under another file name)")
            await self._set_job(job_id, status="diffed" if changed else "unchanged",
                                finished_at=None if changed else now())
            await self._log(job_id, None, "info", "awaiting approval" if changed else "nothing to upgrade")
        except Exception as exc:
            log.exception("preparing upgrade job %s failed", job_id)
            await self._fail(job_id, redact(str(exc)))

    async def approve(self, job_id: int, user: str, window=None) -> None:
        if window and (problem := window_problem(*window, self.clock())):
            raise UpgradeError(problem)
        async with self._lock:
            async with self.sessions() as session:
                job = await session.get(Job, job_id)
                if job is None or job.kind != "upgrade":
                    raise UpgradeError(f"no upgrade job {job_id}")
                if job.status != "diffed":
                    raise UpgradeError(f"job {job_id} is {job.status}, not awaiting approval")
                if (busy := await rolling_out(self.sessions)) is not None and not window:
                    raise UpgradeError(f"job {busy} is still rolling out; one rollout at a time")
                await self._targets(None, [s.device for s in job.steps if s.status == "diffed"])
                job.status, job.approved_by, job.approved_at = "scheduled" if window else "approved", user, now()
                job.window_start, job.window_end = window or (None, None)
                await session.commit()
        await self._approved(job_id, user, window)

    # -- rollout ----------------------------------------------------------------------------------

    async def _rollout(self, job_id: int) -> None:
        try:
            await self._roll(job_id)
        finally:
            self._cancelled.pop(job_id, None)
            await self.deploys._drain()   # deploys queued behind this rollout

    async def _recheck(self, job: Job, steps: list[DeployStep], generators=None) -> str | None:
        """At a window's start, before any switch changes: every switch's plan is still the approved one."""
        await self._log(job.id, None, "info", "checking every switch against the approved plans")
        system, patch = await self._images(job.params)
        differ = []
        for step in steps:
            try:
                plan = await self.plan(step.device, await self._address(step.device), system, patch)
            except Exception as exc:
                return f"{step.device}: {redact(str(exc))}; nothing was upgraded"
            if plan is None or plan.commands() != approved_commands(step.patch):
                differ.append(step.device)
        if differ:
            return (f"the flash or startup files of {', '.join(differ)} changed since the plan was approved; "
                    "nothing was upgraded")
        return None

    async def _roll(self, job_id: int) -> None:
        job = await self._job(job_id)
        await self._set_job(job_id, status="deploying")
        steps = [s for s in job.steps if s.status == "diffed"]
        if job.window_start is not None and (problem := await self._recheck(job, steps)):
            for step in steps:
                await self._set_step(step.id, status="skipped")
            await self._fail(job_id, problem)
            return
        for index, step in enumerate(steps):
            if stop := self._stop_reason(job):
                await self._stop(job, stop, steps[index:])
                return
            try:
                await self._upgrade_one(job_id, step, job.params)
            except Exception as exc:
                reason = redact(str(exc))
                await self._set_step(step.id, status="failed", error=reason, finished_at=now())
                await self._log(job_id, step.device, "error", reason)
                for later in steps[index + 1:]:
                    await self._set_step(later.id, status="skipped")
                    await self._log(job_id, later.device, "warn", "skipped: an earlier switch failed")
                await self._fail(job_id, f"{step.device}: {reason.splitlines()[0]}")
                return
        await self._set_job(job_id, status="verified", finished_at=now())
        await self._news(job, "verified", "every switch upgraded and verified")
        await audit(self.sessions, job.approved_by, "upgrade.verified", job_id)
        await self._log(job_id, None, "info", "all switches upgraded and verified")

    async def _upgrade_one(self, job_id: int, step: DeployStep, params: dict) -> None:
        device = step.device
        say = lambda message: self._log(job_id, device, "info", message)   # noqa: E731
        await self._set_step(step.id, status="upgrading", started_at=now())
        system, patch = await self._images(params)
        ip = await self._address(device)
        plan = await self.plan(device, ip, system, patch)
        if plan is None or plan.commands() != approved_commands(step.patch):
            raise UpgradeError("the switch's flash or startup files changed since the plan was approved; "
                               "request a new upgrade")
        if await self.annet.diff(device):
            raise UpgradeError("the switch is out of sync: the reboot saves the running config, so deploy "
                               "or fix it first")
        await self._peer_ready(device)   # before the long downloads too, not only before the reboot
        links_before = await self.verifier.links_up(device)
        checks = [plan.verify(n, h) for n, h in plan.checks]
        if checks or plan.deletes:
            await say(f"checking {len(checks)} copy(ies) on flash, deleting {len(plan.deletes)} old image(s)")
            await self.annet.commands(device, ip, checks + [switch_cli.delete(n) for n, _ in plan.deletes])
        host, port = server()
        for name, size, sha256 in plan.downloads:
            await say(f"downloading {name} ({_mb(size)})")
            login, password = self.accounts.grant([name])
            self._downloading[step.job_id] = {"device": device, "file": name, "size": size, "login": login,
                                              "started": time.time()}
            try:
                await self.annet.commands(device, ip, [switch_cli.SYSTEM_VIEW,
                                                       switch_cli.download(host, port, login, password, name, size),
                                                       switch_cli.RETURN])
            finally:
                self.accounts.revoke(login)
                self._downloading.pop(step.job_id, None)
            if not switch_cli.has_digest(plan.release):   # R023: the exact size first, then the package check
                (listing,) = await self.annet.commands(device, ip, [switch_cli.size_of(name)])
                got = switch_cli.flash(listing)[0].get(name)
                if got != size:
                    raise UpgradeError(f"flash:/{name} is {got} bytes after the download, not {size}")
            await self.annet.commands(device, ip, [plan.verify(name, sha256)])
            await say(f"{name} downloaded, " + ("digest matched" if switch_cli.has_digest(plan.release)
                                                 else "size and integrity check passed"))
        *_, boot = await self.annet.commands(device, ip, [switch_cli.startup(k, n) for k, n in plan.startups]
                                             + [switch_cli.DISPLAY_STARTUP])
        boot = switch_cli.startup_files(boot)
        for kind, name in plan.startups:
            if boot.get(f"next {kind}") != name:
                raise UpgradeError(f"next startup {kind} is {boot.get(f'next {kind}')}, not {name}")
        await say("next startup set: " + ", ".join(n for _, n in plan.startups))
        await self._peer_ready(device)
        await self._set_step(step.id, status="rebooting")
        await say("rebooting")
        try:
            await self.annet.commands(device, ip, [switch_cli.REBOOT])
        except Exception as exc:   # the session goes down with the switch; whether it rebooted is seen below
            log.info("reboot of %s: %s", device, exc)
        await self._wait(device, ip, up=False, timeout=self.settings.down_timeout)
        await self._wait(device, ip, up=True, timeout=self.settings.up_timeout)
        back = time.time()
        await self._set_step(step.id, status="verifying")
        await say("back on SSH; verifying (release, diff, collector, links)")
        await self._verify(device, ip, system, patch, links_before, back)
        await self._record_in_sync(job_id, device)
        await self._set_step(step.id, status="verified", finished_at=now())
        await say("verified on " + " ".join(i.release for i in (system, patch) if i))

    async def _verify(self, device, ip, system, patch, links_before, back: float) -> None:
        deadline = time.monotonic() + self.settings.verify_timeout
        while True:
            try:   # SSH may answer before login works, links take a while to come up
                (out,) = await self.annet.commands(device, ip, [switch_cli.DISPLAY_VERSION])
                release, running_patch = switch_cli.version(out)
                if system and release != system.release or patch and running_patch != patch.release:
                    raise UpgradeError(f"the switch came back on {release}"
                                       + (f" with patch {running_patch}" if running_patch else ""))
                await self.verifier.verify(device, links_before, back)
                return
            except UpgradeError:
                raise
            except Exception as exc:
                if time.monotonic() > deadline:
                    raise VerificationFailed(f"not verified {self.settings.verify_timeout:.0f} s after it came "
                                             f"back: {exc}") from exc
                await asyncio.sleep(self.settings.poll)

    async def _peer_ready(self, device: str) -> None:
        """Its M-LAG peer is up and healthy: active, polled within a minute, the
        heartbeat and every peer-link up. A planned peer does not count."""
        me = await self.netbox.find_one("dcim/devices", name=device)
        if not (peer_id := _ref_id(_cf(me or {}, "mlag_peer"))):
            return
        peer = await self.netbox.find_one("dcim/devices", id=peer_id)
        if peer is None or status_of(peer) != "active":
            return
        name = peer["name"]
        prometheus = self.verifier.prometheus
        if prometheus is None:
            raise UpgradeError("cannot check the M-LAG peer: Prometheus is not configured")
        last, up, _ = await prometheus.device_state(name)
        if not up or last is None or time.time() - last > self.settings.peer_max_age:
            raise UpgradeError(f"M-LAG peer {name} has not been polled in the last "
                               f"{self.settings.peer_max_age:.0f} s: not rebooting its partner")
        heartbeat = await prometheus.query(f'fabric_mlag_heartbeat_up{{device="{name}"}}')
        links = await prometheus.query(f'fabric_mlag_peer_link_up{{device="{name}"}}')
        if not heartbeat or heartbeat[0][2] != 1 or not links or any(v != 1 for _, _, v in links):
            raise UpgradeError(f"M-LAG peer {name}: heartbeat or peer-link down: not rebooting its partner")

    async def _reachable(self, ip: str) -> bool:
        """SSH answers (a TCP connect only: nothing is sent)."""
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(ip, 22), 5)
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        return True

    async def _wait(self, device: str, ip: str, up: bool, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while await self._reachable(ip) != up:
            if time.monotonic() > deadline:
                raise UpgradeError(f"{device} still answers {timeout:.0f} s after the reboot command: it did not reboot"
                                   if not up else f"{device} has not answered SSH for {timeout:.0f} s after its reboot")
            await asyncio.sleep(self.settings.poll)
