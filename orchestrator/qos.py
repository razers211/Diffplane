"""QoS: one queue plan on every port of the fabric, and DSCP marks.

Intent lives in the fabric's config context (`fabric-<site>`, key `qos`), written only here, read by
the Qos and BridgeDomains generators:

    {"enabled": false,
     "segments": {"<L2 VNI>": "storage" | "scavenger"},
     "ports": {"<switch>": {"<port or Eth-Trunk>": "storage" | "scavenger"}}}

Off by default: nothing reaches a switch until QoS is turned on. The plan itself (trust DSCP, the
queues and their weights) is platform code (generators.qos); intent only says where traffic is
marked. A mark on an M-LAG trunk is written for both leaves of the pair, as segment attachments are.
"""

from generators.qos import MARKS
from orchestrator import documents
from orchestrator.fabric_ntp import _context, _update_fabric
from orchestrator.segments import SegmentError, Segments, _ref_id, context_name

KEY = "qos"


def empty() -> dict:
    return {"enabled": False, "segments": {}, "ports": {}}


async def document(nb, fabric: str) -> dict:
    data = ((await _context(nb, fabric)).get("data") or {}).get("fabric") or {}
    return {**empty(), **(data.get(KEY) or {})}


async def _save(nb, fabric: str, doc: dict) -> None:
    await _update_fabric(nb, fabric, {KEY: doc}, drop=(KEY,))


def _mark(mark: str) -> str:
    if mark not in MARKS:
        raise SegmentError(f"mark {mark!r}: {' or '.join(MARKS)}")
    return mark


async def get(nb, fabric: str) -> dict:
    return await document(nb, fabric)


async def set_enabled(nb, fabric: str, enabled: bool) -> dict:
    doc = await document(nb, fabric)
    await _save(nb, fabric, {**doc, "enabled": bool(enabled)})
    return {"enabled": bool(enabled)}


async def set_segment(nb, fabric: str, vni: int, mark: str | None) -> dict:
    """Mark everything entering the segment's bridge domain (None: no mark, hosts' DSCP trusted)."""
    doc = await document(nb, fabric)
    context = await documents.find(nb, context_name(fabric))
    if str(vni) not in (((context or {}).get("data") or {}).get("segments") or {}):
        raise SegmentError(f"no segment {vni} in fabric {fabric}")
    segments = {k: v for k, v in doc["segments"].items() if k != str(vni)}
    if mark is not None:
        segments[str(vni)] = _mark(mark)
    await _save(nb, fabric, {**doc, "segments": segments})
    return {"vni": vni, "mark": mark}


async def set_port(nb, fabric: str, device_name: str, interface: str, mark: str | None) -> dict:
    """Mark everything entering a port or Eth-Trunk (an M-LAG trunk on both leaves of its pair).
    Never a trunk member (its trunk is marked), a routed fabric link, the peer-link or MEth."""
    doc = await document(nb, fabric)
    device = await nb.find_one("dcim/devices", name=device_name)
    if device is None or (device.get("site") or {}).get("slug") != fabric:
        raise SegmentError(f"no switch {device_name!r} in fabric {fabric}")
    iface = await nb.find_one("dcim/interfaces", device_id=device["id"], name=interface)
    if iface is None:
        raise SegmentError(f"{device_name} has no interface {interface!r}")
    routed = bool(await nb.find("ipam/ip-addresses", interface_id=iface["id"]))
    if (reason := Segments._refusal(device_name, iface, routed)) is not None:
        raise SegmentError(reason.replace("carry segments", "take a mark").replace("attach to", "mark"))
    targets = [device_name]
    segments = Segments(nb)
    mirror = await segments._mirror(device, iface) if _ref_id(device.get("site")) is not None else None
    if mirror is not None:
        targets.append(mirror[0]["name"])
    ports = {name: dict(entries) for name, entries in doc["ports"].items()}
    for name in targets:
        own = ports.setdefault(name, {})
        own.pop(interface, None)
        if mark is not None:
            own[interface] = _mark(mark)
        if not own:
            ports.pop(name)
    await _save(nb, fabric, {**doc, "ports": ports})
    return {"switches": targets, "port": interface, "mark": mark}


async def restore(nb, fabric: str, doc: dict) -> None:
    """Rollback: the document as at the rollback point."""
    await _save(nb, fabric, {**empty(), **doc})
