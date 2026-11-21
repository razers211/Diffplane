"""Local accounts: scrypt password hashes, server-side sessions, audit trail."""

import asyncio
import hashlib
import hmac
import os
import re
import secrets
import threading
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator.db import AuditEvent, Session, User, now

COOKIE = "fabric_session"
def session_ttl() -> timedelta:
    """How long a sign-in lasts; read when used (a GUI setting since)."""
    return timedelta(hours=float(os.environ.get("SESSION_TTL_HOURS", "12")))
# OWASP's minimum for scrypt: N=2^17, r=8, p=1 (128 MiB per hash). Hashes keep their own
# parameters, so older ones still verify; a sign-in upgrades them (needs_rehash).
_SCRYPT = {"n": 2**17, "r": 8, "p": 1}
_MAXMEM = 2**28   # hashlib refuses more than 32 MiB unless told
_HASHING = threading.BoundedSemaphore(2)   # at most two hashes at once: memory stays bounded


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, maxmem=_MAXMEM, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), dklen=32, n=int(n), r=int(r), p=int(p),
                               maxmem=_MAXMEM)
    return hmac.compare_digest(candidate.hex(), digest)


def needs_rehash(stored: str) -> bool:
    return stored.split("$")[1:4] != [str(v) for v in _SCRYPT.values()]


async def _hashing(function, *args):
    """A hash in a worker thread (about 0.3 s): the server keeps answering meanwhile."""
    def limited():
        with _HASHING:
            return function(*args)
    return await asyncio.to_thread(limited)


# Checked against when there is no account to check, so every refusal costs one hash: the
# answer's timing never tells whether a username exists.
_NOBODY = hash_password(secrets.token_hex(16))


class Throttle:
    """Failed sign-ins per client address in a sliding window: past `limit`, the address waits.
    Per address only, never per username, so nobody can lock another person out."""

    def __init__(self, limit: int = 10, window: float = 900.0):
        self.limit, self.window = limit, window
        self.failures: dict[str, list[float]] = {}

    def _recent(self, key: str, at: float) -> list[float]:
        recent = [t for t in self.failures.get(key, []) if at - t < self.window]
        if recent:
            self.failures[key] = recent
        else:
            self.failures.pop(key, None)
        return recent

    def wait(self, key: str, at: float) -> float:
        """Seconds before this address may try again; 0 when it may now."""
        recent = self._recent(key, at)
        return recent[0] + self.window - at if len(recent) >= self.limit else 0.0

    def failed(self, key: str, at: float) -> None:
        self.failures[key] = self._recent(key, at) + [at]


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def audit(sessions: async_sessionmaker, username: str | None, action: str,
                job_id: int | None = None, detail: str | None = None) -> None:
    from orchestrator.drafts import REPLAY
    if (replay := REPLAY.get()) is not None and replay.netbox is not None:
        return   # a change set's step tried on a copy changed nothing
    async with sessions() as session:
        session.add(AuditEvent(username=username, action=action, job_id=job_id, detail=detail))
        await session.commit()


# One role per user, on a ladder: each role can do what the ones
# before it can. viewer: read only; operator: change intent, run diffs, request deploys;
# approver: also approve deploys (their own requests included); admin:
# also accounts, settings, per-fabric deploy permission and deleting a fabric.
ROLES = ("viewer", "operator", "approver", "admin")


USERNAME = re.compile(r"[A-Za-z0-9._@-]{1,100}")
SITE = re.compile(r"[a-z0-9_-]{1,100}")
LIMITABLE = ("viewer", "operator")   # admins and approvers reach every fabric


def check_fabrics(role: str, fabrics: dict | None) -> dict | None:
    """The fabrics an account is limited to, cleaned: None (every fabric) or {site: may approve}."""
    if fabrics is None:
        return None
    if role not in LIMITABLE:
        raise ValueError(f"an {role} reaches every fabric: only viewers and operators are limited to fabrics")
    if not fabrics:
        raise ValueError("a limited account needs at least one fabric")
    if not all(isinstance(s, str) and SITE.fullmatch(s) for s in fabrics):
        raise ValueError("fabrics are given by their short names (slugs)")
    if role == "viewer" and any(fabrics.values()):
        raise ValueError("a viewer cannot approve: make the account an operator first")
    return {site: bool(approve) for site, approve in sorted(fabrics.items())}


def role_allows(role: str, needed: str) -> bool:
    return role in ROLES and ROLES.index(role) >= ROLES.index(needed)


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")


NO_PASSWORD = "!"   # a directory account's password_hash: never matches (verify_password)


class Accounts:
    def __init__(self, sessions: async_sessionmaker, directory=None):
        self.sessions = sessions
        self.directory = directory   # orchestrator.directory.Directory, set by the app

    @staticmethod
    def _local_only(user: User, what: str) -> None:
        if user.source != "local":
            raise ValueError(f"{user.username} comes from the directory ({user.source}): its {what} is set there, "
                             "at each sign-in")

    async def create_user(self, username: str, password: str, role: str = "viewer", actor: str | None = None) -> None:
        _check_role(role)
        username = username.strip()
        if not USERNAME.fullmatch(username):
            raise ValueError("a username is 1-100 letters, digits, '.', '_', '-' or '@'")
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        async with self.sessions() as session:
            if await session.scalar(select(User).where(User.username == username)) is not None:
                raise ValueError(f"user {username!r} already exists")
            session.add(User(username=username, password_hash=await _hashing(hash_password, password), role=role))
            await session.commit()
        await audit(self.sessions, actor, "user.create", detail=f"{username} role={role}")

    async def _admins_left(self, session, without: str) -> int:
        return len([u for u in await session.scalars(select(User).where(User.role == "admin", User.is_active))
                    if u.username != without])

    async def set_role(self, username: str, role: str, actor: str | None = None) -> None:
        _check_role(role)
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.username == username))
            if user is None:
                raise ValueError(f"no user {username!r}")
            self._local_only(user, "role")
            if user.role == "admin" and role != "admin" and not await self._admins_left(session, username):
                raise ValueError(f"{username} is the last admin: make someone else admin first")
            user.role = role
            if role not in LIMITABLE:
                user.fabrics = None                                          # every fabric
            elif role == "viewer" and user.fabrics:
                user.fabrics = {site: False for site in user.fabrics}        # viewers never approve
            await session.commit()
        await audit(self.sessions, actor, "user.role", detail=f"{username} role={role}")

    async def set_fabrics(self, username: str, fabrics: dict | None, actor: str | None = None) -> None:
        """Limit a viewer or operator to fabrics; None: every fabric."""
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.username == username))
            if user is None:
                raise ValueError(f"no user {username!r}")
            self._local_only(user, "fabric access")
            user.fabrics = check_fabrics(user.role, fabrics)
            await session.commit()
            limit = user.fabrics
        detail = "every fabric" if limit is None else \
            ", ".join(f"{s}{' (approves)' if a else ''}" for s, a in limit.items())
        await audit(self.sessions, actor, "user.fabrics", detail=f"{username}: {detail}")

    async def set_password(self, username: str, password: str, actor: str | None = None) -> None:
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.username == username))
            if user is None:
                raise ValueError(f"no user {username!r}")
            self._local_only(user, "password")
            user.password_hash = await _hashing(hash_password, password)
            await session.execute(delete(Session).where(Session.user_id == user.id))   # signed out everywhere
            await session.commit()
        await audit(self.sessions, actor, "user.password", detail=username)

    async def change_own_password(self, username: str, current: str, new: str) -> None:
        """A user changing their own password must give the current one."""
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.username == username))
            if user is None or not await _hashing(verify_password, current, user.password_hash):
                raise ValueError("the current password is not right")
        await self.set_password(username, new, actor=username)

    async def delete_user(self, username: str, actor: str | None = None) -> None:
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.username == username))
            if user is None:
                raise ValueError(f"no user {username!r}")
            if user.role == "admin" and not await self._admins_left(session, username):
                raise ValueError(f"{username} is the last admin: make someone else admin first")
            await session.execute(delete(Session).where(Session.user_id == user.id))
            await session.delete(user)
            await session.commit()
        await audit(self.sessions, actor, "user.delete", detail=username)

    async def list_users(self) -> list[dict]:
        async with self.sessions() as session:
            users = (await session.scalars(select(User).order_by(User.username))).all()
        return [{"username": u.username, "role": u.role, "active": u.is_active, "created_at": u.created_at.isoformat(),
                 "fabrics": u.fabrics, "source": u.source} for u in users]

    async def count(self) -> int:
        async with self.sessions() as session:
            return await session.scalar(select(func.count(User.id)))

    async def login(self, username: str, password: str) -> str | None:
        """A new session token, or None. Failed logins are audited too. A local account is checked
        here only; any other name is asked of the directory, which writes the account's role
        and fabrics at every sign-in."""
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.username == username))
        if user is None or user.source != "local":
            return await self._directory_login(username, password)
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.username == username))
            matches = await _hashing(verify_password, password, user.password_hash if user is not None else _NOBODY)
            if user is None or not user.is_active or not matches:
                await audit(self.sessions, username, "login.failed")
                return None
            if needs_rehash(user.password_hash):   # saved with the session below
                user.password_hash = await _hashing(hash_password, password)
            return await self._session(session, user, username)

    async def _session(self, session, user: User, username: str) -> str:
        token = secrets.token_urlsafe(32)
        session.add(Session(token_hash=_token_hash(token), user_id=user.id, expires_at=now() + session_ttl()))
        await session.commit()
        await audit(self.sessions, username, "login")
        return token

    async def _directory_login(self, username: str, password: str) -> str | None:
        name = username.lower()   # AD names are case-insensitive: one platform record per person
        if self.directory is None or not self.directory.providers():
            await _hashing(verify_password, password, _NOBODY)
            await audit(self.sessions, username, "login.failed")
            return None
        async with self.sessions() as session:
            taken = await session.scalar(select(User).where(User.username == name))
        if taken is not None and taken.source == "local":
            await audit(self.sessions, username, "login.failed", detail="a local account has this name")
            return None
        result = await self.directory.authenticate(name, password)
        if result.outcome != "granted":
            if result.outcome == "no-access" and taken is not None:
                async with self.sessions() as session:   # removed from its groups: no more sign-ins
                    record = await session.get(User, taken.id)
                    record.is_active = False
                    await session.commit()
            await audit(self.sessions, username, "login.failed", detail=f"{result.outcome}: {result.detail}"[:500])
            return None
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.username == name))
            if user is None:
                user = User(username=name, password_hash=NO_PASSWORD)
                session.add(user)
            user.source, user.role, user.fabrics, user.is_active = result.source, result.role, result.fabrics, True
            await session.commit()
            await session.refresh(user)
            return await self._session(session, user, name)

    async def user_for(self, token: str | None) -> User | None:
        if not token:
            return None
        async with self.sessions() as session:
            found = await session.scalar(select(Session).where(Session.token_hash == _token_hash(token)))
            if found is None or found.expires_at < now() or not found.user.is_active:
                return None
            return found.user

    async def logout(self, token: str | None) -> None:
        if not token:
            return
        async with self.sessions() as session:
            await session.execute(delete(Session).where(Session.token_hash == _token_hash(token)))
            await session.commit()
