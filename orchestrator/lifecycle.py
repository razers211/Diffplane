"""A switch's lifecycle in a fabric: activate and decommission.

The GUI owns a device's status and management address from the start:
the seed creates devices as planned and never writes either.

- Activate: the management interface (MEth) gets its address in the global table, set as the
  device's primary IPv4, and the device becomes `active`. Nothing connects to it until its host
  keys are pinned.
- Decommission: back to `planned`; the management address is freed and the pins forgotten, so
  the collector, drift, deploys and the known-hosts file drop it. Its fabric addressing and its
  config on the switch stay.
"""

import ipaddress
import re

from orchestrator.hostkeys import FIELD as PINS
from orchestrator.netbox import NetBoxClient
from orchestrator.segments import SegmentError

MGMT = re.compile(r"MEth\d+/\d+/\d+$")


def _status(device: dict) -> str | None:
    status = device.get("status")
    return status.get("value") if isinstance(status, dict) else status


def _id(value) -> int | None:
    return value.get("id") if isinstance(value, dict) else value


class Lifecycle:
    def __init__(self, netbox: NetBoxClient):
        self.nb = netbox

    async def _device(self, name: str) -> dict:
        device = await self.nb.find_one("dcim/devices", name=name)
        if device is None:
            raise SegmentError(f"no device {name!r}")
        return device

    async def activate(self, name: str, address: str, interface: str = "MEth0/0/0", allow_ztp: bool = False) -> dict:
        """Give a device its management address and make it active (or change the address)."""
        if not MGMT.match(interface):
            raise SegmentError(f"the management interface is a MEth port, not {interface!r}")
        try:
            iface_addr = ipaddress.IPv4Interface(address)
        except ValueError as exc:
            raise SegmentError(f"invalid address: {exc}") from exc
        if "/" not in address or iface_addr.network.prefixlen >= 31 or iface_addr.ip in (
                iface_addr.network.network_address, iface_addr.network.broadcast_address):
            raise SegmentError("give the management address with its mask, e.g. 172.24.0.104/24")
        device = await self._device(name)
        if (device.get("custom_fields") or {}).get("ztp") and not allow_ztp:
            raise SegmentError(f"{name} is registered for zero-touch provisioning: approve its keys there, "
                               "or unregister it first")
        # Management addresses live in the global table and are unique across fabrics.
        for ip in await self.nb.find("ipam/ip-addresses", address=str(iface_addr.ip)):   # any mask
            if ip.get("vrf") is None and _id(ip.get("assigned_object_id")) is not None:
                owner = await self._owner(ip)
                if owner != name:
                    raise SegmentError(f"{iface_addr.ip} is already the management address of {owner}")
        ifaces = await self.nb.find("dcim/interfaces", device_id=device["id"])
        iface = next((i for i in ifaces if i["name"] == interface), None)
        if iface is None:
            iface = await self.nb.create("dcim/interfaces", {"device": device["id"], "name": interface,
                                                             "type": "1000base-t", "mgmt_only": True})
        current = [ip for i in ifaces if MGMT.match(i["name"])
                   for ip in await self.nb.find("ipam/ip-addresses", interface_id=i["id"])]
        ip = next((c for c in current if _id(c.get("assigned_object_id")) == iface["id"]), None)
        if ip is None:
            ip = await self.nb.create("ipam/ip-addresses", {"assigned_object_type": "dcim.interface",
                                                            "assigned_object_id": iface["id"], "status": "active",
                                                            "address": str(iface_addr)})
        elif ip["address"] != str(iface_addr):
            ip = await self.nb.update("ipam/ip-addresses", ip["id"], {"address": str(iface_addr)})
        for other in current:   # an address on another MEth port of the device
            if other["id"] != ip["id"]:
                await self.nb.delete("ipam/ip-addresses", other["id"])
        await self.nb.update("dcim/devices", device["id"], {"primary_ip4": ip["id"], "status": "active"})
        return {"device": name, "status": "active", "interface": interface, "address": str(iface_addr),
                "pinned": bool((device.get("custom_fields") or {}).get(PINS))}

    async def _owner(self, ip: dict) -> str | None:
        iface = await self.nb.find("dcim/interfaces", id=ip.get("assigned_object_id"))
        if not iface:
            return None
        device = await self.nb.find("dcim/devices", id=_id(iface[0].get("device")))
        return device[0]["name"] if device else None

    async def decommission(self, name: str) -> dict:
        """Back to planned: management address freed, pins forgotten; the switch is untouched."""
        device = await self._device(name)
        if _status(device) != "active":
            raise SegmentError(f"{name} is not active")
        # The primary IP goes first: NetBox refuses to delete an address still set as primary.
        await self.nb.update("dcim/devices", device["id"], {"primary_ip4": None, "status": "planned",
                                                            "custom_fields": {PINS: None}})
        freed = []
        for iface in await self.nb.find("dcim/interfaces", device_id=device["id"]):
            if MGMT.match(iface["name"]):
                for ip in await self.nb.find("ipam/ip-addresses", interface_id=iface["id"]):
                    await self.nb.delete("ipam/ip-addresses", ip["id"])
                    freed.append(ip["address"])
        return {"device": name, "status": "planned", "freed": freed}
