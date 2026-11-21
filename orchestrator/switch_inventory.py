"""What a switch is, read from the switch itself.

The CE6800, CE8800 and CE9800 run one software with one syntax; only their ports differ. So the
platform reads each switch's model, release, patch, serial and front-panel ports over NETCONF (the
collector's read account, read-only: two `<get>`s in one session) and keeps NetBox in step:

- the model's ports become its device type's interface templates (`seed.ports.refresh` makes them
  the model's layout: a second switch of the model can then be planned before it exists);
- the device takes the model it reports, its serial, every port it has, each with a NetBox type
  from the port's own hardware (`hard-type`: `Eth10GEPort` is RJ45, `Eth10GFPort` a fibre cage;
  `medium-type` is not used: an empty cage reports `notSupported`).

A port NetBox has and the switch does not is removed, unless it is in use (cabled, in a trunk,
routed, carrying segments or a service, named or turned off): then nothing is changed and the
switch is reported, and the check `port-layout` (orchestrator/checks.py) blocks deploy approval.
Facts, not intent: no deploy follows from them, but generators see the new ports at the next diff.
Breakout ports (`100GE1/0/1:1`) and line-card chassis are out of scope.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from annet.annlib.netdev.views.hardware import HardwareView

from collector.hardware import model as reported_model
from orchestrator import faceplate as fp
from orchestrator.segments import SegmentError
from seed import ports
from seed.loader import MANUFACTURER

SYSTEM_NS = "urn:huawei:yang:huawei-system"
DEVM_NS = "urn:huawei:yang:huawei-devm"
SYSTEM = (f'<system xmlns="{SYSTEM_NS}"><system-info><hardware-model/><product-name/><product-version/><patch-version/><esn/>'
          f'</system-info></system>')
PORTS = f'<devm xmlns="{DEVM_NS}"><ports><port><position/><hard-type/></port></ports></devm>'

HARD_TYPE = re.compile(r"Eth(\d+)([GM])([EF])Port")
COPPER = {100: "100base-tx", 1000: "1000base-t", 2500: "2.5gbase-t", 5000: "5gbase-t", 10000: "10gbase-t"}
FIBRE = {1000: "1000base-x-sfp", 10000: "10gbase-x-sfpp", 25000: "25gbase-x-sfp28", 40000: "40gbase-x-qsfpp",
         50000: "50gbase-x-sfp56", 100000: "100gbase-x-qsfp28", 200000: "200gbase-x-qsfp56",
         400000: "400gbase-x-qsfpdd", 800000: "800gbase-x-qsfpdd"}


def netbox_type(hard_type: str) -> str:
    """`Eth10GEPort` -> 10gbase-t, `Eth100GFPort` -> 100gbase-x-qsfp28; anything else `other`."""
    m = HARD_TYPE.fullmatch(hard_type or "")
    if m is None:
        return "other"
    mbps = int(m.group(1)) * (1000 if m.group(2) == "G" else 1)
    return (COPPER if m.group(3) == "E" else FIBRE).get(mbps, "other")


@dataclass
class Found:
    model: str
    version: str
    patch: str
    serial: str
    ports: dict[str, str] = field(default_factory=dict)   # front-panel port -> NetBox type, in order
    skipped: list[str] = field(default_factory=list)      # ports out of scope (breakout)


def _text(element, ns: str, name: str) -> str:
    found = element.find(f"{{{ns}}}{name}")
    return (found.text or "").strip() if found is not None else ""


def parse(system_xml: str, ports_xml: str) -> Found:
    info = ET.fromstring(system_xml).find(f".//{{{SYSTEM_NS}}}system-info")
    if info is None or not _text(info, SYSTEM_NS, "hardware-model"):
        raise SegmentError("the switch did not report its model (huawei-system system-info)")
    model = reported_model(_text(info, SYSTEM_NS, "hardware-model"), _text(info, SYSTEM_NS, "product-name"))
    # Annet commits only on a model it takes for a CE or NE: on any other it types the patch and quits.
    hw = HardwareView(f"{MANUFACTURER} {model}", "")
    if not (hw.Huawei.CE or hw.Huawei.NE):
        raise SegmentError(f"model {model!r} is not one Annet commits on (a CE or NE model name)")
    found = Found(model, _text(info, SYSTEM_NS, "product-version"),
                  _text(info, SYSTEM_NS, "patch-version"), _text(info, SYSTEM_NS, "esn"))
    named = {}
    for port in ET.fromstring(ports_xml).iter(f"{{{DEVM_NS}}}port"):
        name = _text(port, DEVM_NS, "position")
        if fp.FRONT_PANEL.fullmatch(name):
            named[name] = netbox_type(_text(port, DEVM_NS, "hard-type"))
        elif ":" in name:
            found.skipped.append(name)
    found.ports = dict(sorted(named.items(), key=lambda kv: ports._port_key(kv[0])))
    if not found.ports:
        raise SegmentError(f"the switch ({found.model}) reported no front-panel port")
    return found


async def read(reader, name: str) -> Found:
    system_xml, ports_xml = await reader.get_state(name, [SYSTEM, PORTS])
    return parse(system_xml, ports_xml)


def read_at(host: str, keys: list[str], login: str, password: str, timeout: float = 60.0) -> Found:
    """The same two `<get>`s on a switch not in NetBox yet (New fabric, Add switches), at `host`,
    whose NETCONF host key must be one of `keys`, the ones an approver approved."""
    from ncclient import manager
    from ncclient.transport.errors import SSHUnknownHostError

    from orchestrator import hostkeys

    allowed = {hostkeys.md5_fingerprint(key) for key in keys}
    try:
        with manager.connect(host=host, port=hostkeys.NETCONF_PORT, username=login, password=password,
                             hostkey_verify=True, unknown_host_cb=lambda _, fingerprint: fingerprint in allowed,
                             allow_agent=False, look_for_keys=False, device_params={"name": "default"},
                             timeout=timeout) as session:
            system_xml, ports_xml = (session.get(filter=("subtree", s)).data_xml for s in (SYSTEM, PORTS))
    except SSHUnknownHostError as exc:
        raise SegmentError(f"{host}: the switch presents another NETCONF host key than the approved one") from exc
    except SegmentError:
        raise
    except Exception as exc:   # transport, login, RPC errors: a message for the page
        raise SegmentError(f"{host}: reading its model and ports failed: {type(exc).__name__}: {exc}") from exc
    return parse(system_xml, ports_xml)


async def device_type(nb, model: str) -> dict:
    manufacturer = await nb.find_one("dcim/manufacturers", slug=MANUFACTURER.lower())
    if manufacturer is None:
        manufacturer = await nb.create("dcim/manufacturers", {"name": MANUFACTURER, "slug": MANUFACTURER.lower()})
    found = await nb.find_one("dcim/device-types", manufacturer_id=manufacturer["id"], model=model)
    return found or await nb.create("dcim/device-types", {"manufacturer": manufacturer["id"], "model": model,
                                                           "slug": model.lower()})


async def templates(nb, device_type: dict, layout: dict[str, str]) -> None:
    """The model's ports as its device type's interface templates: exactly what the switch reported."""
    existing = {t["name"]: t for t in await nb.find("dcim/interface-templates", device_type_id=device_type["id"])}
    for name, kind in layout.items():
        if name not in existing:
            await nb.create("dcim/interface-templates", {"device_type": device_type["id"], "name": name, "type": kind})
        elif (existing[name].get("type") or {}).get("value") != kind:
            await nb.update("dcim/interface-templates", existing[name]["id"], {"type": kind})
    for name, t in existing.items():
        if name not in layout and fp.FRONT_PANEL.fullmatch(name):
            await nb.delete("dcim/interface-templates", t["id"])


def _used(port: dict) -> bool:
    return port["in_use"] or port["off"] or bool(port["description"])


async def plan(nb, name: str, found: Found) -> dict:
    """What learning would change on this device, and the ports in use that block it."""
    device = await nb.find_one("dcim/devices", name=name)
    if device is None:
        raise SegmentError(f"no device {name!r}")
    interfaces = {i["name"]: i for i in await nb.find("dcim/interfaces", device_id=device["id"])
                  if fp.FRONT_PANEL.fullmatch(i["name"]) and (i.get("type") or {}).get("value") not in ("lag", "virtual")}
    face = {p["name"]: p for p in (await fp.faceplate(nb, name))["ports"]}
    gone = [p for p in interfaces if p not in found.ports]
    return {
        "device": name, "model": found.model, "version": found.version, "patch": found.patch,
        "serial": found.serial, "was_model": (device.get("device_type") or {}).get("model"),
        "added": [p for p in found.ports if p not in interfaces],
        "retyped": [p for p in found.ports if p in interfaces
                    and (interfaces[p].get("type") or {}).get("value") != found.ports[p]],
        "removed": gone, "blocked": [p for p in gone if p in face and _used(face[p])],
        "skipped": found.skipped, "_device": device, "_interfaces": interfaces,
    }


async def learn(nb, name: str, found: Found) -> dict:
    """Write what the switch reported: the model's layout always, the device unless blocked."""
    result = await plan(nb, name, found)
    device, interfaces = result.pop("_device"), result.pop("_interfaces")
    dtype = await device_type(nb, found.model)
    await templates(nb, dtype, found.ports)
    changed = bool(result["added"] or result["retyped"] or result["removed"]
                   or result["was_model"] != found.model or (found.serial and device.get("serial") != found.serial))
    if result["blocked"]:
        result["changed"] = False
    elif changed:
        update = {"device_type": dtype["id"]} if result["was_model"] != found.model else {}
        if found.serial and device.get("serial") != found.serial:
            update["serial"] = found.serial
        if update:
            await nb.update("dcim/devices", device["id"], update)
        for port in result["removed"]:
            await nb.delete("dcim/interfaces", interfaces[port]["id"])
        for port in result["retyped"]:
            await nb.update("dcim/interfaces", interfaces[port]["id"], {"type": found.ports[port]})
        for port in result["added"]:
            await nb.create("dcim/interfaces", {"device": device["id"], "name": port, "type": found.ports[port]})
        result["changed"] = True
    else:
        result["changed"] = False
    await ports.refresh(nb)
    return result


def summary(result: dict) -> str:
    """One line for the audit trail and the log."""
    parts = [f"{result['device']}: {result['model']} {result['version']} {result['patch']}".rstrip()]
    if result["was_model"] != result["model"]:
        parts.append(f"model was {result['was_model']}")
    for key in ("added", "retyped", "removed"):
        if result[key]:
            parts.append(f"{len(result[key])} port(s) {key}")
    if result["blocked"]:
        parts.append("not changed: ports in use the switch does not have: " + ", ".join(result["blocked"]))
    return "; ".join(parts)


class Sweep:
    """Every active, pinned switch read again every INVENTORY_INTERVAL_SECONDS (default 3600; 0: off),
    so a replaced switch, a new release or a wrong model shows up; skipped while a rollout runs
    (new ports would change what a rollout's recheck generates)."""

    def __init__(self, sessions, netbox, interval: float | None = None):
        import os

        self.sessions, self.nb = sessions, netbox
        self.interval = float(os.environ.get("INVENTORY_INTERVAL_SECONDS", "3600")) if interval is None else interval
        self._task = None

    def start(self) -> None:
        import asyncio

        if self.interval > 0:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        import asyncio

        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        import asyncio
        import logging

        await asyncio.sleep(60)   # after start-up, once the collector has its sessions
        while True:
            try:
                await self.run_once()
            except Exception:   # NetBox or the database away: next time
                logging.getLogger(__name__).exception("inventory: sweep failed")
            await asyncio.sleep(self.interval)

    async def run_once(self, reader=None) -> list[dict]:
        import logging

        from orchestrator import hostkeys
        from orchestrator.auth import audit
        from orchestrator.deploys import rolling_out
        from orchestrator.routes import RouteReader

        if await rolling_out(self.sessions):
            return []
        reader = reader or RouteReader(self.nb)
        results = []
        for device in await self.nb.find("dcim/devices", status="active"):
            if hostkeys.netconf_check(device) is None or hostkeys.address(device) is None:
                continue
            try:
                result = await learn(self.nb, device["name"], await read(reader, device["name"]))
            except Exception as exc:   # one switch unreachable: the others still read
                logging.getLogger(__name__).warning("inventory: %s: %s", device["name"], exc)
                continue
            if result["changed"] or result["blocked"]:
                await audit(self.sessions, None, "switch.inventory", detail=summary(result))
            results.append(result)
        return results
