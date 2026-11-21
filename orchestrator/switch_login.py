"""People's logins to switches over RADIUS.

The intent is `fabric.switch_login` in the fabric context, off by default; the shared key is
write-only and kept in the platform database (table `settings`, key `radius-key:<site>`), masked
through `redact.EXTRA` and given to Annet's runs in RADIUS_KEYS. The domain, and a key once set,
change only with switch login off and deployed: the generator owns one domain name, and a key is
compared by presence, so a new one would never show in a diff.
"""

import asyncio
import ipaddress
import json
import os
import re
from datetime import datetime, timezone

from sqlalchemy import select

from orchestrator import documents
from orchestrator import redact
from orchestrator.db import Setting
from orchestrator.netbox import NetBoxClient
from orchestrator.segments import SegmentError

PREFIX = "radius-key:"
ACCOUNTS = "local-passwords:"
DEFAULT_DOMAIN = "radius"
RESERVED = ("default", "default_admin")   # the platform's accounts sign in there
DOMAIN = re.compile(r"[a-z][a-z0-9-]{0,31}")
KEYS: dict[str, str] = {}
PASSWORDS: dict[str, dict[str, str]] = {}
NAME = re.compile(r"[a-z][a-z0-9._-]{5,31}")   # VRP: six characters at least (a test switch)
LOCAL_USER = re.compile(r"^\s*local-user (\S+) ", re.M)


def remember(site: str, key: str | None) -> None:
    KEYS.pop(site, None)
    if key:
        KEYS[site] = key
        redact.EXTRA.add(key)
    os.environ["RADIUS_KEYS"] = json.dumps(KEYS)   # every Annet run inherits the environment


def remember_passwords(site: str, passwords: dict[str, str]) -> None:
    PASSWORDS[site] = passwords
    redact.EXTRA.update(passwords.values())
    os.environ["LOCAL_PASSWORDS"] = json.dumps(PASSWORDS)


async def load(sessions) -> None:
    """At start: the keys for Annet's runs, masked from then on."""
    async with sessions() as session:
        for row in await session.scalars(select(Setting).where(Setting.key.startswith(PREFIX))):
            remember(row.key.removeprefix(PREFIX), row.value)
        for row in await session.scalars(select(Setting).where(Setting.key.startswith(ACCOUNTS))):
            remember_passwords(row.key.removeprefix(ACCOUNTS), json.loads(row.value))


async def _store(sessions, name: str, value: str, user: str) -> None:
    async with sessions() as session:
        row = await session.get(Setting, name)
        now = datetime.now(timezone.utc)
        if row is None:
            session.add(Setting(key=name, value=value, updated_by=user, updated_at=now))
        else:
            row.value, row.updated_by, row.updated_at = value, user, now
        await session.commit()


async def _save(sessions, site: str, key: str, user: str) -> None:
    await _store(sessions, PREFIX + site, key, user)
    remember(site, key)


async def _context(nb: NetBoxClient, site: str) -> tuple[dict, dict]:
    context = await documents.find(nb, f"fabric-{site}")
    if context is None:
        raise SegmentError(f"no fabric {site!r} (seed it first)")
    return context, ((context.get("data") or {}).get("fabric") or {}).get("switch_login") or {}


async def get(nb: NetBoxClient, site: str) -> dict:
    _, login = await _context(nb, site)
    return {"enabled": bool(login.get("enabled")), "domain": login.get("domain") or DEFAULT_DOMAIN,
            "servers": login.get("servers") or [], "key_set": site in KEYS,
            "accounts": await accounts(nb, site)}


async def accounts(nb: NetBoxClient, site: str) -> list[dict]:
    held = (await _accounts(nb, site))[1].get("accounts") or {}
    return [{"name": name, "level": level} for name, level in sorted(held.items())]


async def _accounts(nb: NetBoxClient, site: str) -> tuple[dict, dict]:
    context, _ = await _context(nb, site)
    return context, (context["data"]["fabric"].get("local_accounts") or {})


async def _write_accounts(nb: NetBoxClient, context: dict, held: dict) -> None:
    data = dict(context.get("data") or {})
    await documents.save(nb, context, {**data, "fabric": {**data["fabric"], "local_accounts": held}})


def _platform_logins() -> set[str]:
    return {os.environ.get(n, "").lower() for n in ("GNETCLI_DEV_LOGIN", "COLLECTOR_LOGIN", "ZTP_ADMIN_LOGIN")} - {""}


async def add_account(nb: NetBoxClient, sessions, annet, site: str, name: str, level: int, password: str, user: str) -> dict:
    """A person's account on every active switch of the fabric. Never a platform account, and never a
    name a switch already has: owning it would take over someone's hand-made account."""
    if not NAME.fullmatch(name):
        raise SegmentError("a name takes 6 to 32 lower-case letters, digits, ., _ and -, starting with a letter")
    if name in _platform_logins():
        raise SegmentError(f"{name} is one of the platform's own accounts")
    if level not in (1, 3):
        raise SegmentError("the level is 1 (can look) or 3 (can change config)")
    if not 8 <= len(password) <= 128 or re.search(r"[\s?'\x00-\x1f]", password):
        raise SegmentError("the password takes 8 to 128 characters, no spaces, ? or '")
    context, held = await _accounts(nb, site)
    if name in (held.get("accounts") or {}):
        raise SegmentError(f"{name} already exists")
    if name not in (held.get("owned") or []):
        devices = sorted(d["name"] for d in await nb.find("dcim/devices", status="active", has_primary_ip="true", site=site))
        try:
            configs = await asyncio.gather(*(annet.current(d) for d in devices))
        except Exception as exc:
            raise SegmentError(f"cannot check the switches for that name: {redact.redact(str(exc))[:200]}") from exc
        if on := [d for d, config in zip(devices, configs) if name in LOCAL_USER.findall(config)]:
            raise SegmentError(f"{name} already exists on {', '.join(on)}, made by hand: it stays yours, pick another name")
    passwords = {**PASSWORDS.get(site, {}), name: password}
    await _store(sessions, ACCOUNTS + site, json.dumps(passwords, sort_keys=True), user)
    remember_passwords(site, passwords)
    await _write_accounts(nb, context, {"site": site, "accounts": {**(held.get("accounts") or {}), name: level},
                                        "owned": sorted({*(held.get("owned") or []), name})})
    return await get(nb, site)


async def remove_account(nb: NetBoxClient, sessions, site: str, name: str, user: str) -> dict:
    context, held = await _accounts(nb, site)
    if name not in (held.get("accounts") or {}):
        raise SegmentError(f"no account {name} made here")
    # shortcut: `owned` never shrinks, so a name stays the platform's after its removal; clear it after a verified deploy if that ever matters
    await _write_accounts(nb, context, {**held, "accounts": {k: v for k, v in held["accounts"].items() if k != name}})
    passwords = {k: v for k, v in PASSWORDS.get(site, {}).items() if k != name}
    await _store(sessions, ACCOUNTS + site, json.dumps(passwords, sort_keys=True), user)
    remember_passwords(site, passwords)
    return await get(nb, site)


def _checked(domain: str, servers: list[str], key: str | None) -> None:
    if domain in RESERVED or not DOMAIN.fullmatch(domain):
        raise SegmentError("the domain takes lower-case letters, digits and -, up to 32, and cannot be "
                           + " or ".join(RESERVED))
    if not 1 <= len(servers) <= 2 or len(set(servers)) != len(servers):
        raise SegmentError("give one or two different RADIUS servers")
    for server in servers:
        try:
            ipaddress.IPv4Address(server)
        except ValueError:
            raise SegmentError(f"{server!r} is not an IPv4 address") from None
    if key is not None and not 8 <= len(key) <= 128 or key and re.search(r"\s", key):
        raise SegmentError("the shared key takes 8 to 128 characters, no spaces")


async def set_login(nb: NetBoxClient, sessions, site: str, enabled: bool, domain: str, servers: list[str],
                    key: str | None, user: str) -> dict:
    context, login = await _context(nb, site)
    if enabled:
        _checked(domain, servers, key)
        if login.get("enabled") and domain != login["domain"]:
            raise SegmentError("the domain changes only with switch login off and deployed")
        if login.get("domain") and domain != login["domain"]:
            raise SegmentError(f"deploy the removal of {login['domain']} first: it cannot be changed before")
        if login.get("enabled") and key:
            raise SegmentError("a new key needs switch login off and deployed first: a key cannot be compared")
        if not key and site not in KEYS:
            raise SegmentError("give the shared key")
        if key:
            await _save(sessions, site, key, user)
        login = {"site": site, "enabled": True, "domain": domain, "servers": servers}
    elif login:
        login = {**login, "enabled": False}
    else:
        return await get(nb, site)
    data = dict(context.get("data") or {})
    await documents.save(nb, context, {**data, "fabric": {**data["fabric"], "switch_login": login}})
    return await get(nb, site)
