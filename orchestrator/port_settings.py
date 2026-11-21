"""Port settings: name, on/off, (Expert) frame size and, on copper ports,
speed of server ports and server Eth-Trunks.

Intent is NetBox's own interface fields: `description`, `enabled` and `mtu` (here the port's jumbo
frame size: none is the switch's default 9216; on these layer-2 ports VRP's `mtu` is the routed
MTU and unused), and `speed` on a copper port (kbit/s; none: negotiated, the default:
auto or a fixed 10M/100M/1G/10G). A 10GE SFP+ port and an Eth-Trunk have no `speed`
command, so only copper ports (their NetBox type, `seed.ports.is_copper`) take one; a trunk's members
are not set (they are trunk members).

Per fabric, off by default (fabric context key `port_profiles`). Turning it on **adopts** what the
active switches have into NetBox first (the preview lists it), so the first diff is empty; from then
on the PortProfiles generator owns those lines by kind on server ports. The adoption reads the running
config (`annet show current`, in memory only: it holds secrets) rather than NETCONF: the YANG gives
the default frame size (9216) and admin status whether or not a line sets them, so an explicit
`jumboframe enable 9216` would be taken for none and removed.

An M-LAG trunk's settings are written on both switches of its pair (off on both).
"""

import asyncio
import re

from orchestrator import documents
from orchestrator.fabric_ntp import _context, _update_fabric
from seed.ports import is_copper
from orchestrator.segments import MLAG_TAG, PEER_LINK_TAG, UNNUMBERED_TAG, SegmentError, Segments, _cf, _ref_id

KEY = "port_profiles"
PHYSICAL = re.compile(r"\d*GE\d+/\d+/\d+")
FRAME = (1518, 15360)   # `jumboframe enable` range; the default 9216
DESCRIPTION_MAX = 242
SPEEDS = (10, 100, 1000, 10000)   # Mbit/s (the switch's help)
DEFAULT = {"description": "", "enabled": True, "frame": None, "speed": None}
UNSET = object()


def _tags(obj: dict) -> set[str]:
    return {t.get("slug") for t in obj.get("tags") or [] if isinstance(t, dict)}


def _kind(iface: dict) -> str | None:
    return (iface.get("type") or {}).get("value")


async def document(nb, fabric: str) -> dict:
    data = ((await _context(nb, fabric)).get("data") or {}).get("fabric") or {}
    return {"enabled": False, **(data.get(KEY) or {})}


async def _excluded(nb, fabric: str) -> dict[str, dict[str, str]]:
    """{switch: {port: why}} for ports a port profile never applies to beyond what NetBox shows:
    DCI uplinks and ports carrying the management network (an import's `management`)."""
    from orchestrator import dci

    overrides, links = await asyncio.gather(documents.find(nb, f"overrides-{fabric}"),
                                            dci.document(nb))
    found: dict[str, dict[str, str]] = {}
    devices = ((((overrides or {}).get("data") or {}).get("overrides") or {}).get("devices") or {})
    for name, values in devices.items():
        for sub in ((values.get("management") or {}).get("interfaces") or ()):
            found.setdefault(name, {})[str(sub).split(".")[0]] = "it carries the management network"
    for link in links.get("links") or ():
        for side in link.get("sides") or ():
            if side.get("fabric") == fabric:
                for switch, uplinks in (side.get("uplinks") or {}).items():
                    for u in uplinks:
                        found.setdefault(switch, {})[u["port"]] = f"it is a DC link uplink ({link['name']})"
    return found


def refusal(device: str, iface: dict, routed: bool, excluded: dict[str, str]) -> str | None:
    """Why this interface takes no port settings, or None (a server port or server trunk)."""
    name, kind = iface["name"], _kind(iface)
    if kind == "lag" and name.startswith("Eth-Trunk"):
        pass
    elif kind == "virtual" or not PHYSICAL.fullmatch(name):
        return f"{device} {name}: only front-panel ports and server Eth-Trunks have port settings"
    elif iface.get("lag"):
        return f"{device} {name} is a trunk member: set it on its trunk"
    if PEER_LINK_TAG in _tags(iface):
        return f"{device} {name} is the M-LAG peer-link"
    if routed or UNNUMBERED_TAG in _tags(iface):
        return f"{device} {name} is a fabric link"
    if name in excluded:
        return f"{device} {name}: {excluded[name]}"
    return None


def settings_of(iface: dict) -> dict:
    return {"description": iface.get("description") or "", "enabled": iface.get("enabled", True) is not False,
            "frame": iface.get("mtu"), "speed": iface["speed"] // 1000 if iface.get("speed") else None}


def _description(value: str) -> str:
    text = value.strip()
    if len(text) > DESCRIPTION_MAX:
        raise SegmentError(f"a port name is at most {DESCRIPTION_MAX} characters")
    if any(not (32 <= ord(c) < 127) or c == "?" for c in text):
        raise SegmentError("a port name takes letters, digits, spaces and punctuation (no '?', no accents)")
    return text


def _frame(value: int | None) -> int | None:
    if value is not None and not FRAME[0] <= value <= FRAME[1]:
        raise SegmentError(f"frame size {value}: {FRAME[0]}-{FRAME[1]} bytes (none: the switch's 9216)")
    return value


def _speed(device: dict, iface: dict, value: int | None) -> int | None:
    """NetBox's speed (kbit/s) for a copper port's fixed speed in Mbit/s, or None (negotiated)."""
    if not is_copper((iface.get("type") or {}).get("value")):
        raise SegmentError(f"{device['name']} {iface['name']} is not a copper port: only copper ports take a speed")
    if value is not None and value not in SPEEDS:
        raise SegmentError(f"speed {value}: one of {', '.join(map(str, SPEEDS))} Mbit/s, or none (auto)")
    return value * 1000 if value else None


async def _port(nb, device_name: str, interface: str) -> tuple[dict, dict, str | None]:
    """(device, interface, why it takes no settings or None)."""
    device = await nb.find_one("dcim/devices", name=device_name)
    if device is None:
        raise SegmentError(f"no switch {device_name!r}")
    fabric = (device.get("site") or {}).get("slug")
    iface, excluded = await asyncio.gather(nb.find_one("dcim/interfaces", device_id=device["id"], name=interface),
                                           _excluded(nb, fabric))
    if iface is None:
        raise SegmentError(f"{device_name} has no interface {interface!r}")
    routed = bool(await nb.find("ipam/ip-addresses", interface_id=iface["id"]))
    return device, iface, refusal(device_name, iface, routed, excluded.get(device_name, {}))


async def impact(nb, device: dict, iface: dict) -> list[str]:
    """What stops when this port or trunk is off: its segments, its service, its trunk's."""
    segments = Segments(nb)
    site = (device.get("site") or {}).get("slug")
    names = {s["vni"]: s["name"] for s in await segments.all(site)} if _cf(iface, "segments") else {}
    found = [f"segment {names.get(a['vni'], a['vni'])}" for a in _cf(iface, "segments") or ()]
    services = await segments._service_ports(_ref_id(device.get("site")))
    if (device["id"], iface["name"]) in services:
        found.append("its service")
    return found


async def get_port(nb, device_name: str, interface: str) -> dict:
    """The port's settings, whether it takes them (and why not), and what turning it off stops."""
    device, iface, reason = await _port(nb, device_name, interface)
    fabric = (device.get("site") or {}).get("slug")
    mirror = await Segments(nb)._mirror(device, iface) if reason is None else None
    return {"device": device_name, "port": interface, **settings_of(iface), "refusal": reason,
            "copper": is_copper((iface.get("type") or {}).get("value")),
            "managed": (await document(nb, fabric))["enabled"], "carries": await impact(nb, device, iface),
            "both": [device_name, mirror[0]["name"]] if mirror else [device_name]}


async def set_port(nb, device_name: str, interface: str, description=UNSET, enabled=UNSET, frame=UNSET,
                   speed=UNSET) -> dict:
    """Change a server port's or trunk's settings (an M-LAG trunk on both switches of its pair)."""
    device, iface, reason = await _port(nb, device_name, interface)
    fabric = (device.get("site") or {}).get("slug")
    if not (await document(nb, fabric))["enabled"]:
        raise SegmentError(f"port settings are not managed in fabric {fabric} yet: turn them on first (Network › Ports)")
    if reason is not None:
        raise SegmentError(reason)
    change = {}
    if description is not UNSET:
        change["description"] = _description(description or "")
    if enabled is not UNSET:
        change["enabled"] = bool(enabled)
    if frame is not UNSET:
        change["mtu"] = _frame(frame)
    if speed is not UNSET:
        change["speed"] = _speed(device, iface, speed)
    if not change:
        raise SegmentError("nothing to change")
    targets = [(device_name, iface)]
    mirror = await Segments(nb)._mirror(device, iface)
    if mirror is not None:
        targets.append((mirror[0]["name"], mirror[1]))
    for _, target in targets:
        await nb.update("dcim/interfaces", target["id"], change)
    return {"switches": [name for name, _ in targets], "port": interface,
            **settings_of({**iface, **change}), "carries": await impact(nb, device, iface)}


# -- turning it on: adoption ----------------------------------------------------------------------

def running_settings(text: str) -> dict[str, dict]:
    """{interface: {description, enabled, frame, speed}} as a running config holds them (only the
    ports with any of these lines; a `speed auto <list>` is not a fixed speed and not adopted)."""
    from orchestrator.routing_import import parse_tree

    found = {}
    for row, children in parse_tree(text).items():
        if not row.startswith("interface "):
            continue
        values = dict(DEFAULT)
        for child in children or {}:
            if child.startswith("description "):
                values["description"] = child[len("description "):].strip()
            elif child == "shutdown":
                values["enabled"] = False
            elif m := re.fullmatch(r"jumboframe enable (\d+)", child):
                values["frame"] = int(m.group(1))
            elif m := re.fullmatch(r"speed (\d+)", child):
                values["speed"] = int(m.group(1))
        if values != DEFAULT:
            found[row[len("interface "):].strip()] = values
    return found


async def adoption(nb, annet, fabric: str) -> dict:
    """What turning port settings on would write into NetBox: each active switch's server ports whose
    running settings differ from NetBox's, read now (nothing is written)."""
    from orchestrator import hostkeys

    devices = [d for d in await nb.find("dcim/devices", site=fabric)
               if (d.get("status") or {}).get("value") == "active"]
    unpinned = [d["name"] for d in devices if not _cf(d, hostkeys.FIELD)]
    if unpinned:
        raise SegmentError(f"host keys not pinned: {', '.join(unpinned)}; pin them first")
    names = [d["name"] for d in devices]
    texts, interfaces, addresses, excluded = await asyncio.gather(
        asyncio.gather(*(annet.current(n) for n in names), return_exceptions=True),
        nb.find("dcim/interfaces", site=fabric),
        asyncio.gather(*(nb.find("ipam/ip-addresses", device_id=d["id"]) for d in devices)), _excluded(nb, fabric))
    failed = [n for n, t in zip(names, texts) if isinstance(t, BaseException)]
    if failed:
        raise SegmentError(f"could not read {', '.join(failed)}: nothing adopted")
    routed = {a.get("assigned_object_id") for found in addresses for a in found}
    changes, ports = [], 0
    for device, text in zip(devices, texts):
        running = running_settings(text)
        for iface in interfaces:
            if _ref_id(iface.get("device")) != device["id"]:
                continue
            if refusal(device["name"], iface, iface["id"] in routed, excluded.get(device["name"], {})) is not None:
                continue
            ports += 1
            have = running.get(iface["name"], DEFAULT)
            if have != settings_of(iface):
                changes.append({"device": device["name"], "port": iface["name"], "id": iface["id"],
                                "netbox": settings_of(iface), "switch": have})
    return {"switches": names, "ports": ports, "changes": changes}


def _fields(values: dict) -> dict:
    """NetBox interface fields for a settings_of() dict."""
    return {"description": values["description"], "enabled": values["enabled"], "mtu": values["frame"],
            "speed": values["speed"] * 1000 if values["speed"] else None}


async def set_enabled(nb, annet, fabric: str, enabled: bool) -> dict:
    """On: adopt what the switches have (read again now), then manage. Off: stop managing; the lines
    stay on the switches and NetBox keeps the values."""
    doc = await document(nb, fabric)
    adopted = []
    if enabled and not doc["enabled"]:
        found = await adoption(nb, annet, fabric)
        for change in found["changes"]:
            have = change["switch"]
            await nb.update("dcim/interfaces", change["id"], _fields(have))
            adopted.append(f"{change['device']} {change['port']}")
    await _update_fabric(nb, fabric, {KEY: {**doc, "enabled": bool(enabled)}}, drop=(KEY,))
    return {"enabled": bool(enabled), "adopted": adopted}


async def restore(nb, device_name: str, interface: str, values: dict) -> None:
    """Rollback: one interface's settings as at the rollback point, written as they were."""
    device = await nb.find_one("dcim/devices", name=device_name)
    iface = device and await nb.find_one("dcim/interfaces", device_id=device["id"], name=interface)
    if not iface:
        raise SegmentError(f"{device_name} has no interface {interface!r} any more")
    await nb.update("dcim/interfaces", iface["id"], _fields({**DEFAULT, **values}))


async def restore_enabled(nb, fabric: str, enabled: bool) -> None:
    """Rollback: managed or not as at the rollback point (no adoption: the ports come back with it)."""
    doc = await document(nb, fabric)
    await _update_fabric(nb, fabric, {KEY: {**doc, "enabled": bool(enabled)}}, drop=(KEY,))
