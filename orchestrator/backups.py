"""Switch config backups.

Each active, pinned switch's running config (`annet show current`, read-only) is kept after every
verified deploy, once a day and on request. A config equal to the switch's latest version (comment
lines aside) adds nothing. The raw text is stored encrypted (Fernet, key `CONFIG_BACKUP_KEY` from
the environment, never in the database or the repo); everything shown is masked: secret values on
their lines and the values of SECRET_ENV. Versions older than 90 days go, except each switch's last
20. Restoring is done through intent, never by sending a stored config to a switch.
"""

import asyncio
import difflib
import hashlib
import logging
import os
import re
import time
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select

from orchestrator import deploys, hostkeys
from orchestrator.db import ConfigVersion, now
from orchestrator.redact import MASK, redact

log = logging.getLogger(__name__)

KEY_ENV = "CONFIG_BACKUP_KEY"
KEEP_DAYS = 90
KEEP_LAST = 20
REASONS = ("scheduled", "deploy", "manual")

# Secret values on VRP lines: VRP cipher text (%^%#...%^%#), hashes ($1a$...), and the word after
# password / cipher / irreversible-cipher / simple and similar keywords.
_CIPHER = re.compile(r"%\^%#.*?%\^%#|%\$%\$.*?%\$%\$|\$\d[a-z]?\$\S+")
_KEYWORD = re.compile(r"\b(password|cipher|irreversible-cipher|simple|authentication-key|shared-key|"
                      r"pre-shared-key|key-string)((?:\s+(?:cipher|irreversible-cipher|simple))*)\s+(\S+)")


class BackupError(Exception):
    pass


def mask(text: str) -> str:
    """The config as the GUI and API may show it: secret values replaced."""
    text = _CIPHER.sub(MASK, text)
    text = _KEYWORD.sub(lambda m: f"{m.group(1)}{m.group(2)} {MASK}" if m.group(3) != MASK else m.group(0), text)
    return redact(text)


def digest(text: str) -> str:
    """Of the config without VRP's comment lines (`!...`, they carry times) and trailing spaces."""
    body = "\n".join(line.rstrip() for line in text.splitlines() if not line.lstrip().startswith("!"))
    return hashlib.sha256(body.encode()).hexdigest()


def cipher(env=os.environ) -> Fernet:
    key = env.get(KEY_ENV)
    if not key:
        raise BackupError(f"{KEY_ENV} is not set: config backups are off (docs/operations.md, Config backups)")
    try:
        return Fernet(key.encode())
    except ValueError:
        raise BackupError(f"{KEY_ENV} is not a valid key (32 bytes, url-safe base64)") from None


def version_json(v: ConfigVersion) -> dict:
    return {"id": v.id, "device": v.device, "site": v.site, "taken_at": v.taken_at.isoformat(), "reason": v.reason,
            "job_id": v.job_id, "requested_by": v.requested_by, "lines": v.lines}


class Backups:
    def __init__(self, sessions, annet, netbox, env=os.environ):
        self.sessions, self.annet, self.netbox, self.env = sessions, annet, netbox, env

    def problem(self) -> str | None:
        try:
            cipher(self.env)
        except BackupError as exc:
            return str(exc)
        return None

    async def capture(self, device: str, reason: str, job_id: int | None = None, user: str | None = None) -> dict:
        """Read the switch's running config and keep it unless unchanged; the version and whether it is new."""
        fernet = cipher(self.env)   # before reading anything: no key, no backup
        found = await self.netbox.find_one("dcim/devices", name=device)
        if found is None or _value(found.get("status")) != "active":
            raise BackupError(f"{device} is not an active switch")
        if not (found.get("custom_fields") or {}).get(hostkeys.FIELD):
            raise BackupError(f"{device}'s host keys are not pinned")
        text = await self.annet.current(device)
        stamp, site = digest(text), _value(found.get("site"))
        async with self.sessions() as session:
            latest = await session.scalar(select(ConfigVersion).where(ConfigVersion.device == device)
                                          .order_by(ConfigVersion.taken_at.desc(), ConfigVersion.id.desc()).limit(1))
            if latest is not None and latest.digest == stamp:
                return {**version_json(latest), "new": False}
            version = ConfigVersion(device=device, site=site, taken_at=now(), reason=reason, job_id=job_id,
                                    requested_by=user, digest=stamp, lines=len(text.splitlines()),
                                    config=fernet.encrypt(text.encode()))
            session.add(version)
            await session.commit()
            result = {**version_json(version), "new": True}
        await self.prune(device)
        return result

    async def prune(self, device: str) -> int:
        """Drop versions older than KEEP_DAYS, always keeping the device's last KEEP_LAST."""
        async with self.sessions() as session:
            ids = (await session.scalars(select(ConfigVersion.id).where(ConfigVersion.device == device)
                                         .order_by(ConfigVersion.taken_at.desc(), ConfigVersion.id.desc()))).all()
            old = ids[KEEP_LAST:]
            if not old:
                return 0
            result = await session.execute(delete(ConfigVersion).where(
                ConfigVersion.id.in_(old), ConfigVersion.taken_at < now() - timedelta(days=KEEP_DAYS)))
            await session.commit()
            return result.rowcount or 0

    async def versions(self, device: str) -> list[dict]:
        async with self.sessions() as session:
            found = (await session.scalars(select(ConfigVersion).where(ConfigVersion.device == device)
                                           .order_by(ConfigVersion.taken_at.desc(), ConfigVersion.id.desc()))).all()
        return [version_json(v) for v in found]

    async def _raw(self, device: str, version_id: int) -> tuple[ConfigVersion, str]:
        async with self.sessions() as session:
            version = await session.get(ConfigVersion, version_id)
        if version is None or version.device != device:
            raise BackupError(f"no version {version_id} of {device}")
        try:
            return version, cipher(self.env).decrypt(version.config).decode()
        except InvalidToken:
            raise BackupError(f"version {version_id} cannot be read: {KEY_ENV} changed since it was taken") from None

    async def shown(self, device: str, version_id: int) -> dict:
        version, text = await self._raw(device, version_id)
        return {**version_json(version), "config": mask(text)}

    async def diff(self, device: str, older: int, newer: int) -> dict:
        (a, text_a), (b, text_b) = await self._raw(device, older), await self._raw(device, newer)
        lines = difflib.unified_diff(mask(text_a).splitlines(), mask(text_b).splitlines(),
                                     f"{device} #{a.id} {a.taken_at:%Y-%m-%d %H:%M}",
                                     f"{device} #{b.id} {b.taken_at:%Y-%m-%d %H:%M}", n=3, lineterm="")
        return {"older": version_json(a), "newer": version_json(b), "diff": "\n".join(lines)}

    async def capture_all(self, reason: str = "scheduled") -> dict[str, str]:
        """Every active, pinned switch, one at a time; what happened per switch."""
        devices = await self.netbox.find("dcim/devices", status="active", has_primary_ip="true")
        results = {}
        for device in sorted(d["name"] for d in devices if (d.get("custom_fields") or {}).get(hostkeys.FIELD)):
            try:
                results[device] = "new" if (await self.capture(device, reason))["new"] else "unchanged"
            except Exception as exc:   # one unreachable switch must not stop the others
                results[device] = f"failed: {redact(str(exc))}"
                log.warning("config backup of %s failed: %s", device, redact(str(exc)))
        return results


def _value(field) -> str | None:
    if isinstance(field, dict):
        return field.get("value") or field.get("slug") or field.get("name")
    return field


class BackupScheduler:
    """A daily backup of every switch (`BACKUP_INTERVAL_SECONDS`, default a day), skipped while a
    deploy rolls out (tried again 5 minutes later)."""

    RETRY = 300

    def __init__(self, backups: Backups, interval: float | None = None):
        self.backups = backups
        self.interval = interval or float(os.environ.get("BACKUP_INTERVAL_SECONDS", 86400))
        self.last_run: float | None = None
        self.last_results: dict[str, str] = {}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def first_delay(self) -> float:
        """Due a day after the last scheduled version; at the earliest 5 minutes after start."""
        async with self.backups.sessions() as session:
            last = await session.scalar(select(ConfigVersion.taken_at).where(ConfigVersion.reason == "scheduled")
                                        .order_by(ConfigVersion.taken_at.desc()).limit(1))
        due = (last.timestamp() + self.interval) if last else 0
        return max(self.RETRY, due - time.time())

    async def _loop(self) -> None:
        await asyncio.sleep(await self.first_delay())
        while True:
            delay = self.interval
            try:
                if self.backups.problem() is not None:
                    pass                                    # no key: nothing to do until it is set
                elif await deploys.rolling_out(self.backups.sessions) is not None:
                    delay = self.RETRY
                else:
                    self.last_results = await self.backups.capture_all()
                    self.last_run = time.time()
            except Exception:   # the scheduler must outlive one bad run
                log.exception("scheduled config backup failed")
            await asyncio.sleep(delay)
