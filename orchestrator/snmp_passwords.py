"""SNMPv3 reader passwords and the job that types them on the switches.

VRP takes a USM user's passwords only interactively (`Enter Password:` / `Confirm Password:`, lab-
leaf1), so no generator can write them: the password job sends them through the
platform's gnetcli exec path (`switch_cli`, as software upgrades), approved like a deploy, one switch
at a time. Per switch: its diff must be empty first (the reader's user exists in its group), then
`authentication-mode` and `privacy-mode` with the prompts answered, `commit`, `save`; verified when
NETCONF shows the reader with its algorithms and the diff is still empty.

Passwords are write-only: kept in the platform database (table `settings`, one row per fabric,
key `snmp-v3:<site>`, never listed by the Settings page), masked in everything the orchestrator
returns (`redact.EXTRA`), never in NetBox, a job's plan, its log or an audit entry.
"""

import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET

from sqlalchemy import select

from orchestrator import redact, switch_cli
from orchestrator.auth import audit
from orchestrator.db import DeployStep, Job, Setting, now
from orchestrator.deploys import DeployError, DeployJobs, rolling_out
from orchestrator.snmp_access import get as get_intent

log = logging.getLogger(__name__)

PREFIX = "snmp-v3:"
CLASSES = (re.compile(r"[a-z]"), re.compile(r"[A-Z]"), re.compile(r"[0-9]"), re.compile(r"[^A-Za-z0-9]"))
SNMP = "urn:huawei:yang:huawei-snmp"
USERS = f'<snmp xmlns="{SNMP}"><usm-users><usm-user><user-name/><group-name/><auth-protocol/><priv-protocol/></usm-user></usm-users></snmp>'


class PasswordError(DeployError):
    pass


def check(kind: str, value: str) -> str:
    """8-64 characters, no spaces, `?` or quotes (VRP's help key and the CLI's), three kinds of
    character of four (the switch's complexity check is on)."""
    if not 8 <= len(value) <= 64 or any(c.isspace() or c in "?\"'" for c in value):
        raise PasswordError(f"{kind} password: 8-64 characters, no spaces, '?' or quotes")
    if sum(bool(c.search(value)) for c in CLASSES) < 3:
        raise PasswordError(f"{kind} password: use three of lower case, upper case, digits and symbols")
    return value


# -- the store ------------------------------------------------------------------------------------

async def _all(sessions, site: str) -> dict:
    async with sessions() as session:
        row = await session.get(Setting, PREFIX + site)
    return json.loads(row.value) if row else {}


async def _save(sessions, site: str, data: dict, user: str) -> None:
    async with sessions() as session:
        row = await session.get(Setting, PREFIX + site)
        value = json.dumps(data, sort_keys=True)
        if row is None:
            session.add(Setting(key=PREFIX + site, value=value, updated_by=user, updated_at=now()))
        else:
            row.value, row.updated_by, row.updated_at = value, user, now()
        await session.commit()
    remember(data)


def remember(data: dict) -> None:
    for passwords in data.values():
        redact.EXTRA.update(v for v in passwords.values() if v)


async def load(sessions) -> None:
    """At start: every stored password is masked from then on."""
    async with sessions() as session:
        for row in await session.scalars(select(Setting).where(Setting.key.startswith(PREFIX))):
            remember(json.loads(row.value))


async def known(sessions, site: str) -> list[str]:
    """Readers of the fabric with passwords stored (never the passwords)."""
    return sorted(await _all(sessions, site))


async def set_passwords(sessions, site: str, readers: dict, reader: str, auth: str, priv: str, user: str) -> None:
    """`readers`: the fabric's readers now (intent); the passwords are typed by the next password job."""
    if reader not in readers:
        raise PasswordError(f"no reader {reader!r} in {site}")
    check("authentication", auth)
    check("encryption", priv)
    if auth == priv:
        raise PasswordError("use two different passwords (the switch warns when they are the same)")
    data = await _all(sessions, site)
    data[reader] = {"auth": auth, "priv": priv}
    await _save(sessions, site, data, user)
    await audit(sessions, user, "snmp.password", detail=f"{site} reader={reader}")


async def forget(sessions, site: str, reader: str, user: str) -> None:
    data = await _all(sessions, site)
    if data.pop(reader, None) is not None:
        await _save(sessions, site, data, user)


async def copy(sessions, site: str, source: str, user: str) -> None:
    data = await _all(sessions, site)
    data.update(await _all(sessions, source))
    await _save(sessions, site, data, user)


# -- the switch side ------------------------------------------------------------------------------

def steps(reader: str, auth: str, priv: str, passwords: dict) -> list[switch_cli.Step]:
    """The switch's dialogue: each mode asks the password twice; nothing else may be asked."""
    def typed(kind: str, mode: str, value: str) -> switch_cli.Step:
        return switch_cli.Step(f"snmp-agent usm-user v3 {reader} {kind} {mode}",
                               (("Enter Password:", value), ("Confirm Password:", value)), hide=(value,))
    return [switch_cli.SYSTEM_VIEW, typed("authentication-mode", auth, passwords["auth"]),
            typed("privacy-mode", priv, passwords["priv"]), switch_cli.Step("commit"), switch_cli.RETURN,
            switch_cli.SAVE]


def plan_text(readers: dict[str, dict]) -> str:
    """What is approved per switch (the passwords never shown)."""
    lines = ["system-view"]
    for name, algorithms in sorted(readers.items()):
        lines += [f"snmp-agent usm-user v3 {name} authentication-mode {algorithms['auth']}   # password typed twice",
                  f"snmp-agent usm-user v3 {name} privacy-mode {algorithms['priv']}   # password typed twice"]
    return "\n".join(lines + ["commit", "return", "save"])


def users(xml: str) -> dict[str, tuple[str, str, str]]:
    """user -> (group, auth protocol, priv protocol), from NETCONF `usm-users`."""
    found = {}
    for user in ET.fromstring(xml).iter(f"{{{SNMP}}}usm-user"):
        text = lambda tag: (user.findtext(f"{{{SNMP}}}{tag}") or "").strip()   # noqa: E731
        found[text("user-name")] = (text("group-name"), text("auth-protocol").lower(), text("priv-protocol").lower())
    return found


def reader_state(wanted: dict, found: tuple[str, str, str] | None) -> str:
    """One reader on one switch: `missing` (no deploy yet), `no-passwords` (no job typed them yet;
    NETCONF says noAuth), `different` (other algorithms) or `ok`. A password itself cannot be read."""
    if found is None:
        return "missing"
    _, auth, priv = found
    if auth in ("", "noauth"):
        return "no-passwords"
    return "ok" if (auth, priv) == (wanted["auth"], wanted["priv"]) else "different"


class PasswordJobs(DeployJobs):
    KIND = "snmp-password"

    def __init__(self, deploys: DeployJobs, reader):
        super().__init__(deploys.sessions, deploys.annet, deploys.netbox, deploys.verifier)
        self.deploys = deploys
        self._lock = deploys._lock
        self.reader = reader   # NETCONF state reads with the collector's account (routes.RouteReader)

    async def recover(self) -> None:
        """A job interrupted by a restart is failed: a switch may hold one password and not the other."""
        async with self.sessions() as session:
            for job in (await session.scalars(select(Job).where(Job.kind == self.KIND, Job.status.in_(
                    ("preparing", "diffed", "approved", "deploying"))))).all():
                for step in job.steps:
                    if step.status == "deploying":
                        step.status, step.finished_at = "failed", now()
                        step.error = "orchestrator restarted while typing the passwords: run the job again"
                    elif step.status in ("pending", "diffed"):
                        step.status = "skipped"
                job.status, job.finished_at, job.error = "failed", now(), "orchestrator restarted during the job"
            await session.commit()

    async def switch_state(self, site: str) -> list[dict]:
        """Each reader on each active switch, read now over NETCONF, nothing stored."""
        intent = (await get_intent(self.netbox, site))["readers"]
        found = await self.netbox.find("dcim/devices", status="active", has_primary_ip="true", site=site)

        async def one(device: str) -> dict:
            try:
                have = users(await self.reader.get_state(device, USERS))
            except Exception as exc:   # one switch not answering must not hide the others
                return {"device": device, "error": f"could not read it: {type(exc).__name__}"}
            return {"device": device, "readers": {r: reader_state(w, have.get(r)) for r, w in intent.items()}}

        return list(await asyncio.gather(*(one(n) for n in sorted(d["name"] for d in found))))

    async def request(self, user: str, site: str, readers: list[str]) -> int:
        intent = (await get_intent(self.netbox, site))["readers"]
        stored = await _all(self.sessions, site)
        if not readers:
            raise PasswordError("choose the readers whose passwords to type")
        missing = [r for r in readers if r not in intent]
        if missing:
            raise PasswordError(f"no reader {', '.join(missing)} in {site}")
        if unset := [r for r in readers if r not in stored]:
            raise PasswordError(f"set the passwords of {', '.join(unset)} first")
        targets = await self._targets(site, None)
        if not targets:
            raise PasswordError(f"{site} has no active switch")
        text = plan_text({r: intent[r] for r in readers})
        async with self.sessions() as session:
            job = Job(kind=self.KIND, status="diffed", site=site, requested_by=user,
                      params={"readers": sorted(readers), "algorithms": {r: intent[r] for r in readers}})
            job.steps = [DeployStep(device=d, position=i, status="diffed", patch=text) for i, d in enumerate(targets)]
            session.add(job)
            await session.commit()
            job_id = job.id
        await audit(self.sessions, user, f"{self.KIND}.request", job_id, f"{site} readers={','.join(sorted(readers))}")
        await self._log(job_id, None, "info", f"requested by {user}: {', '.join(sorted(readers))} on {', '.join(targets)}")
        return job_id

    async def approve(self, job_id: int, user: str, window=None) -> None:
        if window:
            raise PasswordError("a password job runs now, not in a window")
        async with self._lock:
            async with self.sessions() as session:
                job = await session.get(Job, job_id)
                if job is None or job.kind != self.KIND:
                    raise PasswordError(f"no password job {job_id}")
                if job.status != "diffed":
                    raise PasswordError(f"job {job_id} is {job.status}, not awaiting approval")
                if (busy := await rolling_out(self.sessions)) is not None:
                    raise PasswordError(f"job {busy} is still rolling out; one rollout at a time")
                job.status, job.approved_by, job.approved_at = "approved", user, now()
                await session.commit()
        await self._approved(job_id, user, None)

    async def _rollout(self, job_id: int) -> None:
        try:
            await self._roll(job_id)
        finally:
            self._cancelled.pop(job_id, None)
            await self.deploys._drain()   # deploys queued behind it

    async def _roll(self, job_id: int) -> None:
        job = await self._job(job_id)
        await self._set_job(job_id, status="deploying")
        steps_ = [s for s in job.steps if s.status == "diffed"]
        for index, step in enumerate(steps_):
            if stop := self._stop_reason(job):
                await self._stop(job, stop, steps_[index:])
                return
            try:
                await self._one(job_id, step, job.site, job.params)
            except Exception as exc:
                reason = redact.redact(str(exc))
                await self._set_step(step.id, status="failed", error=reason, finished_at=now())
                await self._log(job_id, step.device, "error", reason)
                for later in steps_[index + 1:]:
                    await self._set_step(later.id, status="skipped")
                await self._fail(job_id, f"{step.device}: {reason.splitlines()[0]}")
                return
        await self._set_job(job_id, status="verified", finished_at=now())
        await audit(self.sessions, job.approved_by, f"{self.KIND}.verified", job_id)
        await self._log(job_id, None, "info", "passwords typed and verified on every switch")

    async def _one(self, job_id: int, step: DeployStep, site: str, params: dict) -> None:
        device = step.device
        await self._set_step(step.id, status="deploying", started_at=now())
        if await self.annet.diff(device):
            raise PasswordError("the switch is out of sync: deploy the readers first (their users and group), "
                                "then type the passwords")
        stored = await _all(self.sessions, site)
        found = await self.netbox.find_one("dcim/devices", name=device)
        ip = ((found or {}).get("primary_ip4") or {}).get("address", "").split("/")[0]
        if not ip:
            raise PasswordError(f"{device} has no management address")
        for reader in params["readers"]:
            algorithms = params["algorithms"][reader]
            if reader not in stored:
                raise PasswordError(f"the passwords of {reader} were removed since the job was requested")
            await self.annet.commands(device, ip, steps(reader, algorithms["auth"], algorithms["priv"], stored[reader]))
            await self._log(job_id, device, "info", f"{reader}: passwords typed, committed and saved")
        have = users(await self.reader.get_state(device, USERS))
        for reader in params["readers"]:
            algorithms = params["algorithms"][reader]
            group, auth, priv = have.get(reader, ("", "", ""))
            if (auth, priv) != (algorithms["auth"], algorithms["priv"]):
                raise PasswordError(f"{reader} shows {auth or 'no authentication'} / {priv or 'no encryption'}, "
                                    f"not {algorithms['auth']} / {algorithms['priv']}")
        if await self.annet.diff(device):
            raise PasswordError("the switch is out of sync after the passwords")
        await self._record_in_sync(job_id, device)
        await self._set_step(step.id, status="verified", finished_at=now())
