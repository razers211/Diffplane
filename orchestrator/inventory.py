"""Switches not yet in a fabric: a dedicated NetBox site `inventory`
holding them as planned devices with their model, serial number and MEth MAC.

The GUI never lists the site as a fabric (it has no fabric context). Adding a switch to a
fabric (new fabric or Add switches) creates the fabric's device with the inventory switch's
serial and MAC, then deletes the inventory entry (orchestrator/fabric_build.py). The MAC is what
zero-touch provisioning answers (orchestrator/ztp.py); the GUI offers it when registering.
"""

import re

from orchestrator.netbox import NetBoxClient
from orchestrator.segments import SegmentError
from orchestrator.ztp import normalize_mac
from seed.loader import Seeder
from seed import ports

SITE = "inventory"
ROLE = "inventory"
MAC_FIELD = "meth_mac"
SERIAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,47}\Z")


def name_of(serial: str) -> str:
    """NetBox device names are global (Annet looks devices up by name): an inventory switch
    is named after its serial number, which no fabric switch name looks like."""
    return f"inv-{serial}"


def entry(device: dict) -> dict:
    return {"serial": device.get("serial") or "", "name": device["name"],
            "model": (device.get("device_type") or {}).get("model"),
            "mac": (device.get("custom_fields") or {}).get(MAC_FIELD)}


class Inventory(Seeder):
    def __init__(self, netbox: NetBoxClient):
        super().__init__(netbox, None)

    async def list(self) -> list[dict]:
        return sorted((entry(d) for d in await self.nb.find("dcim/devices", site=SITE)), key=lambda e: e["serial"])

    async def get(self, serial: str) -> dict:
        device = await self.nb.find_one("dcim/devices", site=SITE, name=name_of(serial))
        if device is None:
            raise SegmentError(f"no switch with serial {serial!r} in the inventory")
        return device

    async def _check(self, serial: str | None, model: str, mac: str | None, exclude: int | None = None) -> str | None:
        await ports.refresh(self.nb)
        if not ports.ports_of(model):
            raise SegmentError(f"model {model!r} has no known port layout (a first switch of it is read when it is "
                               f"added with its management address); known: {', '.join(ports.known_models())}")
        if mac:
            mac = normalize_mac(mac)
            for d in await self.nb.find("dcim/devices"):
                if d["id"] != exclude and (d.get("custom_fields") or {}).get(MAC_FIELD) == mac:
                    raise SegmentError(f"MAC {mac} already belongs to {d['name']}")
        return mac or None

    async def add(self, serial: str, model: str, mac: str | None) -> dict:
        serial = (serial or "").strip()
        if not SERIAL.match(serial):
            raise SegmentError("the serial number is 1-48 letters, digits, '.', '_' or '-'")
        if await self.nb.find("dcim/devices", name=name_of(serial)):
            raise SegmentError(f"serial {serial} is already in the inventory")
        mac = await self._check(serial, model, mac)
        site = await self._ensure("dcim/sites", {"slug": SITE},
                                  {"name": "Inventory", "slug": SITE, "status": "active",
                                   "description": "Switches not yet in a fabric (platform inventory)"}, SITE)
        role = await self._ensure("dcim/device-roles", {"slug": ROLE},
                                  {"name": ROLE, "slug": ROLE, "color": "9e9e9e"}, ROLE)
        await mac_field(self)
        await self._device_type(model)
        device = await self.nb.create("dcim/devices", {
            "name": name_of(serial), "serial": serial, "site": site["id"], "role": role["id"],
            "device_type": self._ids[f"type:{model}"], "status": "planned",
            "custom_fields": {MAC_FIELD: mac}})
        return entry(device)

    async def update(self, serial: str, model: str, mac: str | None) -> dict:
        device = await self.get(serial)
        mac = await self._check(serial, model, mac, exclude=device["id"])
        await self._device_type(model)
        updated = await self.nb.update("dcim/devices", device["id"], {
            "device_type": self._ids[f"type:{model}"], "custom_fields": {MAC_FIELD: mac}})
        return entry(updated)

    async def remove(self, serial: str) -> None:
        device = await self.get(serial)
        await self.nb.delete("dcim/devices", device["id"])


async def mac_field(seeder: Seeder) -> None:
    await seeder._ensure(
        "extras/custom-fields",
        {"name": MAC_FIELD},
        {"name": MAC_FIELD, "label": "MEth MAC", "type": "text", "object_types": ["dcim.device"],
         "description": "MAC of the management port (from the inventory; zero-touch provisioning answers it)"},
        MAC_FIELD,
    )
