"""Edge-port protection: storm control, BPDU guard and loop detection on
server ports and server trunks, from a template per port.

Intent lives in the fabric's config context (`fabric-<site>`, key `protection`), written only here,
read by the PortProtection generator:

    {"mode": "off" | "watch" | "on",
     "owned": true,            # set the first time the fabric left Off, never cleared
     "ports": {"<switch>": {"<port or Eth-Trunk>": "server" | "hypervisor"}}}

Off by default: nothing reaches a switch until it is turned to Watch or On. The templates' values are
platform code (generators.protection). A template on an M-LAG trunk is written for both switches of
its pair. Never on a fabric link, trunk member, the peer-link, MEth, a DCI uplink, a management port,
or a firewall's or ISP's port or trunk (those are never protected).
"""

import asyncio

from generators.protection import MODES, TEMPLATES
from orchestrator import documents
from orchestrator import port_settings
from orchestrator.fabric_ntp import _context, _update_fabric
from orchestrator.segments import SegmentError, Segments, _ref_id

KEY = "protection"


def empty() -> dict:
    return {"mode": "off", "ports": {}}


async def document(nb, fabric: str) -> dict:
    data = ((await _context(nb, fabric)).get("data") or {}).get("fabric") or {}
    return {**empty(), **(data.get(KEY) or {})}


async def _save(nb, fabric: str, doc: dict) -> None:
    await _update_fabric(nb, fabric, {KEY: doc}, drop=(KEY,))


async def set_mode(nb, fabric: str, mode: str) -> dict:
    if mode not in MODES:
        raise SegmentError(f"mode {mode!r}: {', '.join(MODES)}")
    doc = await document(nb, fabric)
    owned = bool(doc.get("owned")) or mode != "off"
    await _save(nb, fabric, {**doc, "mode": mode, **({"owned": True} if owned else {})})
    return {"mode": mode}


async def _refusal(nb, device: dict, iface: dict, reason: str | None) -> str | None:
    if reason is not None:
        return reason.replace("have port settings", "can be protected")
    vlan_trunks = await _vlan_trunks(nb, device)
    services = await Segments(nb)._service_ports(_ref_id(device.get("site")))
    if (device["id"], iface["name"]) in services or iface["name"] in vlan_trunks:
        return f"{device['name']} {iface['name']} faces a firewall or ISP: never protected"
    return None


async def _vlan_trunks(nb, device: dict) -> set[str]:
    fabric = (device.get("site") or {}).get("slug")
    overrides = await documents.find(nb, f"overrides-{fabric}")
    devices = ((((overrides or {}).get("data") or {}).get("overrides") or {}).get("devices") or {})
    return set((devices.get(device["name"]) or {}).get("vlan_trunks") or {})


async def set_port(nb, device_name: str, interface: str, template: str | None) -> dict:
    """Protect a server port or trunk with a template (None: unprotected), an M-LAG trunk on both
    switches of its pair."""
    if template is not None and template not in TEMPLATES:
        raise SegmentError(f"template {template!r}: {', '.join(TEMPLATES)} or none")
    device, iface, reason = await port_settings._port(nb, device_name, interface)
    if (why := await _refusal(nb, device, iface, reason)) is not None:
        raise SegmentError(why)
    fabric = (device.get("site") or {}).get("slug")
    doc = await document(nb, fabric)
    targets = [device_name]
    mirror = await Segments(nb)._mirror(device, iface)
    if mirror is not None:
        targets.append(mirror[0]["name"])
    ports = {name: dict(entries) for name, entries in doc["ports"].items()}
    for name in targets:
        own = ports.setdefault(name, {})
        own.pop(interface, None)
        if template is not None:
            own[interface] = template
        if not own:
            ports.pop(name)
    await _save(nb, fabric, {**doc, "ports": ports})
    return {"switches": targets, "port": interface, "template": template}


async def get_port(nb, device_name: str, interface: str) -> dict:
    device, iface, reason = await port_settings._port(nb, device_name, interface)
    doc = await document(nb, (device.get("site") or {}).get("slug"))
    return {"device": device_name, "port": interface, "mode": doc["mode"],
            "template": (doc["ports"].get(device_name) or {}).get(interface),
            "refusal": await _refusal(nb, device, iface, reason)}


async def restore(nb, fabric: str, doc: dict) -> None:
    """Rollback: the document as at the rollback point (keeping `owned` once set)."""
    owned = (await document(nb, fabric)).get("owned") or doc.get("owned")
    await _save(nb, fabric, {**empty(), **doc, **({"owned": True} if owned else {})})


# -- what protection is doing now: the collector's series, read from Prometheus ----------

CAUSE_WORDS = {"storm-control": "storm control", "loopback-detect": "loop detection",
               "bpdu-protection": "BPDU protection (a switch was plugged in)"}
STATE_WORDS = {"error-down": "shut", "err-down": "shut", "block": "blocking the excess", "suppress": "suppressing the excess",
               "alarm": "alarm only (Watch)"}


def trip_text(port: str, trip: dict) -> str:
    """One trip in plain words: `10GE1/0/10 shut by loop detection, reopens in 31 s`."""
    what = CAUSE_WORDS.get(trip["cause"], trip["cause"])
    detail = f" ({trip['detail']})" if trip.get("detail") else ""
    reopen = f", reopens in {trip['reopen']} s" if trip.get("reopen") is not None else ""
    return f"{port}: {what}{detail}, {STATE_WORDS.get(trip['state'], trip['state'])}{reopen}"


async def tripped(prometheus, devices: list[str]) -> dict[tuple[str, str], list[dict]]:
    """(switch, port) -> what protection is doing to it, from the last control poll of each switch."""
    if prometheus is None or not devices:
        return {}
    match = prometheus._devices_match(devices)
    trips, reopens = await asyncio.gather(prometheus.query(f"fabric_port_protection_tripped{{{match}}}"),
                                          prometheus.query(f"fabric_port_protection_reopen_seconds{{{match}}}"))
    left = {(m["device"], m["interface"], m["cause"]): int(v) for m, _, v in reopens}
    found: dict[tuple[str, str], list[dict]] = {}
    for m, _, _ in trips:
        key = (m["device"], m["interface"])
        found.setdefault(key, []).append({"cause": m["cause"], "state": m["state"], "detail": m.get("detail", ""),
                                          "reopen": left.get((*key, m["cause"]))})
    return found
