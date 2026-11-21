"""Staged changes for change windows: intent changes saved for a window
are recorded, not applied, so everyone keeps working on the live intent until the window.

A change set belongs to one fabric. An intent write sent with the header `X-Change-Set: <id>`
(any endpoint behind `INTENT_WRITE`) is replayed through the real endpoint against the change
set's copy of NetBox (orchestrator/netbox_clone.py), so it is checked exactly as a live write
would be; if the endpoint accepts it, the call (method, path, body) is recorded and the live
NetBox is left alone. At the window's start the recorded calls are replayed, in order and as
their author, on the live NetBox (`apply`).

A replay runs in this process through the app itself (ASGI): the `REPLAY` context variable makes
`get_netbox` answer the copy (or the live NetBox), `current_account` answer the author, the
intent freeze let it through (the window's own job is rolling out) and `audit` stay silent on
a copy. Nothing outside this module sets it, so no request from outside can claim it.
"""

import asyncio
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select

from orchestrator import db
from orchestrator.netbox_clone import clone

HEADER = "X-Change-Set"
COPY_SECONDS = 60.0   # a copy older than this is made again, so draft mode follows live changes

# Writes that cannot wait for a window: they talk to switches or other fabrics (and /dci/...), are
# not intent, or change what an intent snapshot cannot restore (switches, roles, pools), so a
# window's start that goes wrong could not take them back out (rollback).
NOT_STAGED = {
    ("PUT", "/devices/{name}/management"), ("DELETE", "/devices/{name}/management"),
    ("PUT", "/devices/{name}/ztp"), ("DELETE", "/devices/{name}/ztp"),
    ("POST", "/fabrics"), ("DELETE", "/fabrics/{site}"),
    ("POST", "/routing/{site}/devices/{device}/import"), ("POST", "/imports/{import_id}/write"),
    ("POST", "/fabrics/{site}/switches"), ("POST", "/fabrics/{site}/spines"), ("POST", "/fabrics/{site}/leaf-pairs"),
    ("DELETE", "/devices/{name}"), ("PUT", "/devices/{name}/role"),
    ("PUT", "/fabrics/{site}/ipv6-pool"), ("PUT", "/fabrics/{site}/relay-pool"), ("PUT", "/fabrics/{site}/pip-pool"),
    ("PUT", "/fabrics/{site}/switch-login"),   # a key, and a rollback cannot take it back
    ("PUT", "/fabrics/{site}/switch-accounts/{name}"), ("DELETE", "/fabrics/{site}/switch-accounts/{name}"),   # the same
    ("PUT", "/fabrics/{site}/ospf-auth"), ("POST", "/fabrics/{site}/ospf-auth/roll"),   # keys, and read the switches
}


@dataclass
class Replay:
    account: db.User
    netbox: Any = None   # a copy; None: the live NetBox
    site: str | None = None   # the change set's fabric: a step on a copy may name no other


REPLAY: ContextVar[Replay | None] = ContextVar("draft_replay", default=None)


class DraftError(Exception):
    pass


class Staged(Exception):
    """Raised by the intent-write dependency once a call is recorded: the endpoint must not run."""

    def __init__(self, change_set: int, step: int, result):
        self.change_set, self.step, self.result = change_set, step, result


def change_set_json(c: db.ChangeSet) -> dict:
    return {"id": c.id, "site": c.site, "author": c.author, "created_at": c.created_at.isoformat(),
            "status": c.status, "steps": c.steps}


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return str(detail or response.text or response.status_code)


class Drafts:
    def __init__(self, sessions, netbox, app):
        self.sessions, self.netbox, self.app = sessions, netbox, app
        self._copies: dict[int, tuple[float, Any]] = {}   # change set id -> (made at, its copy with every step)
        self._locks: dict[int, asyncio.Lock] = {}

    async def create(self, site: str, user: str) -> dict:
        async with self.sessions() as session:
            row = db.ChangeSet(site=site, author=user, status="open", steps=[])
            session.add(row)
            await session.commit()
            return change_set_json(row)

    async def get(self, change_set_id: int) -> dict:
        async with self.sessions() as session:
            row = await session.get(db.ChangeSet, change_set_id)
        if row is None:
            raise DraftError(f"no change set {change_set_id}")
        return change_set_json(row)

    async def list(self, site: str) -> list[dict]:
        async with self.sessions() as session:
            rows = (await session.scalars(select(db.ChangeSet).where(db.ChangeSet.site == site)
                                          .order_by(db.ChangeSet.id.desc()))).all()
        return [change_set_json(r) for r in rows]

    async def set_status(self, change_set_id: int, status: str, expected: tuple[str, ...]) -> dict:
        async with self.sessions() as session:
            row = await session.get(db.ChangeSet, change_set_id)
            if row is None:
                raise DraftError(f"no change set {change_set_id}")
            if row.status not in expected:
                raise DraftError(f"change set {change_set_id} is {row.status}")
            row.status = status
            await session.commit()
        self._copies.pop(change_set_id, None)
        return change_set_json(row)

    def _lock(self, change_set_id: int) -> asyncio.Lock:
        return self._locks.setdefault(change_set_id, asyncio.Lock())

    async def copy(self, change_set_id: int, fresh: bool = False):
        """The fabric as it would be with the change set applied now. `fresh` copies NetBox again
        (a preview, the window's start); otherwise a copy younger than COPY_SECONDS is reused."""
        async with self._lock(change_set_id):   # never cached without a step being staged meanwhile
            return await self._copy(change_set_id, fresh)

    async def _copy(self, change_set_id: int, fresh: bool):
        change_set = await self.get(change_set_id)
        made, copy = self._copies.get(change_set_id, (0.0, None))
        if not fresh and copy is not None and time.monotonic() - made < COPY_SECONDS:
            return copy
        copy = await clone(self.netbox)
        for n, step in enumerate(change_set["steps"], 1):
            response = await self._call(step, copy, site=change_set["site"])
            if response.status_code >= 400:
                raise DraftError(f"step {n} ({step['method']} {step['path']}) is now refused: {_detail(response)}")
        self._copies[change_set_id] = (time.monotonic(), copy)
        return copy

    async def stage(self, change_set_id: int, account: db.User, method: str, path: str, body: bytes) -> Staged:
        """Try one intent write on the change set's copy; record it if the endpoint accepts it."""
        async with self._lock(change_set_id):
            change_set = await self.get(change_set_id)
            if change_set["status"] != "open":
                raise DraftError(f"change set {change_set_id} is {change_set['status']}: it can no longer change")
            copy = await self._copy(change_set_id, False)
            step = {"method": method, "path": path, "body": body.decode() if body else None, "user": account.username,
                    "at": db.now().isoformat()}
            response = await self._call(step, copy, account, change_set["site"])
            if response.status_code >= 400:
                self._copies.pop(change_set_id, None)   # a refused write may have changed the copy halfway
                raise StepRefused(response.status_code, _detail(response))
            async with self.sessions() as session:
                row = await session.get(db.ChangeSet, change_set_id)
                row.steps = [*row.steps, step]
                await session.commit()
            result = response.json() if response.content else None
            return Staged(change_set_id, len(change_set["steps"]) + 1, result)

    async def apply(self, change_set_id: int) -> None:
        """Replay every step on the live NetBox, in order; stops at the first refusal (DraftError)."""
        change_set = await self.get(change_set_id)
        for n, step in enumerate(change_set["steps"], 1):
            response = await self._call(step, None)
            if response.status_code >= 400:
                raise DraftError(f"step {n} ({step['method']} {step['path']}) was refused: {_detail(response)}")

    async def _call(self, step: dict, netbox, account: db.User | None = None, site: str | None = None) -> httpx.Response:
        if account is None:   # replayed as its author, with the author's rights of today
            async with self.sessions() as session:
                account = await session.scalar(select(db.User).where(db.User.username == step["user"]))
            if account is None:
                return httpx.Response(403, json={"detail": f"user {step['user']} no longer exists"})
        token = REPLAY.set(Replay(account, netbox, site))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://replay") as client:
                return await client.request(step["method"], step["path"], content=step["body"],
                                            headers={"content-type": "application/json"} if step["body"] else {})
        finally:
            REPLAY.reset(token)


class StepRefused(DraftError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


async def subprocess_patches(site: str, copy, configs: dict[str, str]) -> dict[str, str]:
    """Each switch's patch on a copy (orchestrator/draft_preview.py, in its own process)."""
    from orchestrator.imports import _subprocess_preview

    request = {"site": site, "objects": copy.objects, "configs": configs}
    return (await _subprocess_preview(request, "orchestrator.draft_preview"))["patches"]
