"""Who may read a fabric's switches over SNMP: v2c with the platform's
community and/or SNMPv3 readers, and the trap targets that send to them.

Intent lives next to the location and contact in the fabric context (`fabric.snmp`): `v2c`
(default on), `readers` {name: {auth, priv}}, `traps` {name: {address, reader}} and `owned`
(every reader and trap the platform ever wrote, so the Snmp generator removes them when they go;
grows, never shrinks, as syslog's). Readers are users of the read-only group FABRIC-RO with
authentication and encryption (SHA2-256 + AES256 by default).

Passwords never reach NetBox: VRP takes them only interactively, so the password job types them
(`orchestrator/snmp_passwords.py`). They are kept write-only in the platform database.
"""

import ipaddress
import re

from orchestrator import fabric_ntp
from orchestrator.netbox import NetBoxClient
from orchestrator.segments import SegmentError

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")   # VRP: user and target host names 1-32
AUTH = ("sha2-256", "sha2-384", "sha2-512")          # strong ones only; the first is the default
PRIV = ("aes256", "aes192", "aes128")


async def get(nb: NetBoxClient, site: str) -> dict:
    snmp = (((await fabric_ntp._context(nb, site)).get("data") or {}).get("fabric") or {}).get("snmp") or {}
    owned = snmp.get("owned") or {}
    return {"location": snmp.get("location"), "contact": snmp.get("contact"), "v2c": snmp.get("v2c", True),
            "readers": dict(snmp.get("readers") or {}), "traps": dict(snmp.get("traps") or {}),
            "owned": {"users": list(owned.get("users") or []), "traps": list(owned.get("traps") or [])}}


async def _write(nb: NetBoxClient, site: str, current: dict) -> dict:
    owned = {"users": sorted({*current["owned"]["users"], *current["readers"]}),
             "traps": sorted({*current["owned"]["traps"], *current["traps"]})}
    snmp = {k: current[k] for k in ("location", "contact") if current.get(k)}
    if not current["v2c"]:
        snmp["v2c"] = False
    snmp.update({k: current[k] for k in ("readers", "traps") if current[k]})
    if owned["users"] or owned["traps"]:
        snmp["owned"] = owned
    await fabric_ntp._update_fabric(nb, site, {"snmp": snmp}, drop=("snmp",))
    return {**current, "owned": owned}


def _name(kind: str, name: str) -> str:
    if not NAME.fullmatch(name):
        raise SegmentError(f"{kind} name: 1-32 letters, digits, '-' or '_', starting with a letter")
    return name


async def set_reader(nb: NetBoxClient, site: str, name: str, auth: str = AUTH[0], priv: str = PRIV[0]) -> dict:
    """Add a reader or change its algorithms (its passwords are then typed again by the password job)."""
    if auth not in AUTH or priv not in PRIV:
        raise SegmentError(f"algorithms: authentication one of {', '.join(AUTH)}, encryption one of {', '.join(PRIV)}")
    current = await get(nb, site)
    current["readers"][_name("reader", name)] = {"auth": auth, "priv": priv}
    return await _write(nb, site, current)


async def delete_reader(nb: NetBoxClient, site: str, name: str) -> dict:
    current = await get(nb, site)
    if name not in current["readers"]:
        raise SegmentError(f"no reader {name!r} in {site}")
    if users := sorted(t for t, trap in current["traps"].items() if trap.get("reader") == name):
        raise SegmentError(f"trap targets {', '.join(users)} send to {name}: remove or move them first")
    if len(current["readers"]) == 1 and not current["v2c"]:
        raise SegmentError("the last reader of a fabric without v2c: turn v2c on first, or nothing could read the switches")
    del current["readers"][name]
    return await _write(nb, site, current)


async def set_trap(nb: NetBoxClient, site: str, name: str, address: str, reader: str) -> dict:
    """A trap target: the monitoring system's IPv4 address, reached over MEth in `_management_vpn_`,
    sent as the reader `reader` (v3 with authentication and encryption, UDP 162)."""
    current = await get(nb, site)
    if reader not in current["readers"]:
        raise SegmentError(f"no reader {reader!r}: traps are sent as one of the fabric's readers")
    try:
        ip = ipaddress.IPv4Address(address.strip())
    except ValueError as exc:
        raise SegmentError(f"trap target address: {exc}") from exc
    if ip.is_multicast or ip.is_loopback or ip.is_unspecified:
        raise SegmentError(f"trap target address {ip}: a host's unicast address is needed")
    if len(current["traps"]) >= 20 and name not in current["traps"]:
        raise SegmentError("at most 20 trap targets (the switch's limit)")
    current["traps"][_name("trap target", name)] = {"address": str(ip), "reader": reader}
    return await _write(nb, site, current)


async def delete_trap(nb: NetBoxClient, site: str, name: str) -> dict:
    current = await get(nb, site)
    if current["traps"].pop(name, None) is None:
        raise SegmentError(f"no trap target {name!r} in {site}")
    return await _write(nb, site, current)


async def set_v2c(nb: NetBoxClient, site: str, on: bool) -> dict:
    """v2c (the platform's community) on or off: off needs a v3 reader, or nothing could read."""
    current = await get(nb, site)
    if not on and not current["readers"]:
        raise SegmentError("add a v3 reader before turning v2c off, or nothing could read the switches")
    current["v2c"] = on
    return await _write(nb, site, current)


async def copy(nb: NetBoxClient, site: str, source: str) -> dict:
    """Readers and trap targets of another fabric (copyable), added to this one's."""
    if source == site:
        raise SegmentError("copy from another fabric")
    theirs, current = await get(nb, source), await get(nb, site)
    current["readers"].update(theirs["readers"])
    current["traps"].update(theirs["traps"])
    if len(current["traps"]) > 20:
        raise SegmentError("at most 20 trap targets (the switch's limit)")
    return await _write(nb, site, current)


async def restore(nb: NetBoxClient, site: str, v2c: bool, readers: dict, traps: dict) -> dict:
    """As at a rollback point; `owned` keeps growing, so what went is removed."""
    current = await get(nb, site)
    current.update(v2c=v2c, readers=dict(readers), traps=dict(traps))
    return await _write(nb, site, current)


def view(current: dict) -> dict:
    return {k: current[k] for k in ("v2c", "readers", "traps")}
