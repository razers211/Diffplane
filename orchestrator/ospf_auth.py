"""OSPF authentication of a fabric's links.

The intent is `fabric.ospf_auth` in the fabric context; the keys are random, write-only and kept in
the platform database (table `settings`, key `ospf-keys:<site>`), masked through `redact.EXTRA`
and given to Annet's runs in OSPF_KEYS. Turning it on or off is refused while a fabric link is up:
the two ends of a link would change at different moments. A roll is three deploys, each step
allowed only once the switches show the previous one (read now over NETCONF).
"""

import asyncio
import json
import os
import secrets
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from sqlalchemy import select

from generators.ospf_auth import KEYCHAIN
from orchestrator import documents
from orchestrator import redact
from orchestrator.db import Setting
from orchestrator.netbox import NetBoxClient
from orchestrator.segments import SegmentError

PREFIX = "ospf-keys:"
NS = "urn:huawei:yang:huawei-keychain"
STATE = (f'<keychain xmlns="{NS}"><keychains><keychain><name>{KEYCHAIN}</name><abs-key-ids><abs-key-id><key-id/>'
         '<default-send-key-id/></abs-key-id></abs-key-ids></keychain></keychains></keychain>')
MAX_KEY_ID = 63
KEYS: dict[str, dict[str, str]] = {}


def remember(site: str, keys: dict[str, str]) -> None:
    KEYS[site] = keys
    redact.EXTRA.update(keys.values())
    os.environ["OSPF_KEYS"] = json.dumps(KEYS)   # every Annet run inherits the environment


async def load(sessions) -> None:
    """At start: the keys for Annet's runs, masked from then on."""
    async with sessions() as session:
        for row in await session.scalars(select(Setting).where(Setting.key.startswith(PREFIX))):
            remember(row.key.removeprefix(PREFIX), json.loads(row.value))


async def _save(sessions, site: str, keys: dict[str, str], user: str) -> None:
    async with sessions() as session:
        row = await session.get(Setting, PREFIX + site)
        now = datetime.now(timezone.utc)
        value = json.dumps(keys, sort_keys=True)
        if row is None:
            session.add(Setting(key=PREFIX + site, value=value, updated_by=user, updated_at=now))
        else:
            row.value, row.updated_by, row.updated_at = value, user, now
        await session.commit()
    remember(site, keys)


async def _context(nb: NetBoxClient, site: str) -> tuple[dict, dict]:
    context = await documents.find(nb, f"fabric-{site}")
    if context is None:
        raise SegmentError(f"no fabric {site!r} (seed it first)")
    return context, ((context.get("data") or {}).get("fabric") or {}).get("ospf_auth") or {}


async def _write(nb: NetBoxClient, context: dict, auth: dict) -> None:
    data = dict(context.get("data") or {})
    await documents.save(nb, context, {**data, "fabric": {**data["fabric"], "ospf_auth": auth}})


def stage(auth: dict) -> str:
    """off, on (one key), added (the new key accepted, the old one sent) or moved (the new one sent)."""
    if not auth.get("enabled"):
        return "off"
    if len(auth["keys"]) == 1:
        return "on"
    return "moved" if auth["send"] == auth["keys"][-1] else "added"


async def get(nb: NetBoxClient, site: str) -> dict:
    _, auth = await _context(nb, site)
    return {"enabled": bool(auth.get("enabled")), "keys": auth.get("keys") or [], "send": auth.get("send"),
            "stage": stage(auth)}


async def _active(nb: NetBoxClient, site: str) -> list[str]:
    return sorted(d["name"] for d in await nb.find("dcim/devices", status="active", has_primary_ip="true", site=site))


async def set_enabled(nb: NetBoxClient, sessions, verifier, site: str, enabled: bool, user: str) -> dict:
    context, auth = await _context(nb, site)
    if enabled == bool(auth.get("enabled")):
        return await get(nb, site)
    devices = await _active(nb, site)
    if devices and verifier.prometheus is None:
        raise SegmentError("cannot tell whether a fabric link is up: no telemetry")
    states = await asyncio.gather(*(verifier.prometheus.device_state(d) for d in devices))
    if blind := [d for d, (_, polled, _) in zip(devices, states) if not polled]:   # no data is not "no link up"
        raise SegmentError(f"cannot tell whether a fabric link is up: {', '.join(blind)} not polled")
    ports = await asyncio.gather(*(verifier.fabric_ports(d) for d in devices))
    up = [f"{d} {port}" for d, cabled, (_, _, oper) in zip(devices, ports, states) for port in cabled if oper.get(port)]
    if up:
        raise SegmentError("turning OSPF authentication on or off takes each link down until both ends have changed, "
                           f"so it needs a fabric with no link up: {', '.join(up[:5])} are up")
    if enabled:
        await _save(sessions, site, {"1": secrets.token_hex(24)}, user)
        auth = {"site": site, "enabled": True, "keys": [1], "send": 1}
    else:
        await _save(sessions, site, {}, user)
        auth = {"site": site, "enabled": False}
    await _write(nb, context, auth)
    return await get(nb, site)


def keys_on_switch(xml: str) -> tuple[set[int], int | None]:
    """(key IDs in the platform's keychain, the one sent by default), from NETCONF."""
    ids, send = set(), None
    for key in ET.fromstring(xml).iter(f"{{{NS}}}abs-key-id"):
        key_id = int(key.findtext(f"{{{NS}}}key-id"))
        ids.add(key_id)
        if (key.findtext(f"{{{NS}}}default-send-key-id") or "").strip() == "true":
            send = key_id
    return ids, send


async def _lagging(nb: NetBoxClient, reader, site: str, check) -> list[str]:
    async def one(device: str) -> str | None:
        try:
            return None if check(*keys_on_switch(await reader.get_state(device, STATE))) else device
        except Exception:   # not proven is not ready: a wrong step drops the links
            return f"{device} (could not be read)"
    return [d for d in await asyncio.gather(*(one(n) for n in await _active(nb, site))) if d]


async def roll(nb: NetBoxClient, sessions, reader, site: str, action: str, user: str) -> dict:
    """`add` a new key, `send` with it once every switch has it, `retire` the old one once all send with it."""
    context, auth = await _context(nb, site)
    current = stage(auth)
    if action == "add" and current == "on":
        new = auth["keys"][-1] + 1
        if new > MAX_KEY_ID:   # shortcut: IDs are not reused (the new ID must stay above the old for `default send-key-id` to move in one patch), 62 rolls is decades
            raise SegmentError(f"no key ID left above {auth['keys'][-1]}")
        await _save(sessions, site, {**KEYS.get(site, {}), str(new): secrets.token_hex(24)}, user)
        auth = {**auth, "keys": [auth["keys"][-1], new]}
    elif action == "send" and current == "added":
        new = auth["keys"][-1]
        if late := await _lagging(nb, reader, site, lambda ids, _send: new in ids):
            raise SegmentError(f"deploy the new key first: not on {', '.join(late)}")
        auth = {**auth, "send": new}
    elif action == "retire" and current == "moved":
        old, new = auth["keys"]
        if late := await _lagging(nb, reader, site, lambda _ids, send: send == new):
            raise SegmentError(f"deploy the move first: {', '.join(late)} still send with key {old}")
        await _save(sessions, site, {k: v for k, v in KEYS.get(site, {}).items() if k != str(old)}, user)
        auth = {**auth, "keys": [new]}
    else:
        raise SegmentError(f"a roll cannot {action} now: it is at {current}")
    await _write(nb, context, auth)
    return await get(nb, site)
