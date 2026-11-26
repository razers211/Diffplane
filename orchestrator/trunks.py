"""Eth-Trunks created from the GUI, written to NetBox.

Three kinds: an M-LAG trunk on a pair (the same trunk on both devices, NetBox tag `m-lag`,
M-LAG ID = trunk number), a single trunk on one leaf, a single trunk on a spine. LACP static
only. The YAML creates a pair's first trunks; afterwards every server trunk is the GUI's, and
the peer-link stays the seed's.

A deleted trunk is retired with its members (routing document, `retired_trunks`) on each
active device: the Trunks generator keeps owning it until a verified deploy removed it.
Hand-made trunks on a switch are never touched: the platform owns only what NetBox knows.
"""

import asyncio
import re

from orchestrator.netbox import NetBoxClient
from orchestrator.segments import MLAG_TAG, PEER_LINK_TAG, UNNUMBERED_TAG, SegmentError, Segments, _cf, _ref_id
from generators import naming
from seed import ports as seed_ports

# Eth-Trunk numbers offered here. The CE6881H's own range is unverified; this stays inside it
# (the lab test covers what is used).
MAX_NUMBER = 1023


def _tags(iface: dict) -> set[str]:
    return {t.get("slug") for t in iface.get("tags") or [] if isinstance(t, dict)}


def _kind(iface: dict) -> str | None:
    return (iface.get("type") or {}).get("value")


def _number(name: str) -> int:
    return naming.trunk_number(name)


class Trunks:
    def __init__(self, netbox: NetBoxClient):
        self.nb = netbox
        self.segments = Segments(netbox)

    # -- reading ---------------------------------------------------------------------------

    async def _device(self, name: str) -> dict:
        device = await self.nb.find_one("dcim/devices", name=name)
        if device is None:
            raise SegmentError(f"no device {name!r}")
        return device

    async def _peer(self, device: dict) -> dict | None:
        from orchestrator.routing_intent import RoutingStore

        name = await RoutingStore(self.nb).peer_of(device)
        return await self._device(name) if name else None

    async def _state(self, device: dict) -> tuple[list[dict], set[int], set[str]]:
        """(interfaces, ids of interfaces with an address, service ports) of a device."""
        interfaces, addresses, ports = await asyncio.gather(
            self.nb.find("dcim/interfaces", device_id=device["id"]),
            self.nb.find("ipam/ip-addresses", device_id=device["id"]),
            self.segments._service_ports(_ref_id(device.get("site"))))
        routed = {ip.get("assigned_object_id") for ip in addresses}
        services = {port for dev, port in ports if dev == device["id"]}
        return interfaces, routed, services

    @staticmethod
    def _free(iface: dict, routed: set[int], services: set[str]) -> bool:
        """A port that may join a new trunk: a front-panel port, in no trunk, with no address
        (not a fabric link), no segments and no service."""
        return (naming.is_front_panel(iface["name"]) and _kind(iface) not in ("lag", "virtual")
                and not iface.get("lag") and iface["id"] not in routed and UNNUMBERED_TAG not in _tags(iface)
                and not _cf(iface, "segments")
                and iface["name"] not in services)

    async def of_device(self, name: str) -> dict:
        """A device's trunks, its free ports, and (on a pair) the ports free on both devices."""
        device = await self._device(name)
        peer = await self._peer(device)
        states = await asyncio.gather(*(self._state(d) for d in (device, peer) if d is not None))
        interfaces, routed, services = states[0]
        members: dict[str, list[str]] = {}
        for iface in interfaces:
            if iface.get("lag"):
                lag = iface["lag"]
                lag_name = lag.get("name") or next((i["name"] for i in interfaces if i["id"] == lag.get("id")), "")
                members.setdefault(lag_name, []).append(iface["name"])
        trunks = []
        for iface in interfaces:
            if _kind(iface) != "lag" or not naming.is_trunk(iface["name"]):
                continue
            tags = _tags(iface)
            trunks.append({
                "name": iface["name"],
                "number": _number(iface["name"]),
                "members": sorted(members.get(iface["name"], []), key=_port_key),
                "kind": "peer-link" if PEER_LINK_TAG in tags else "m-lag" if MLAG_TAG in tags else "single",
                "segments": len(_cf(iface, "segments") or []),
                "service": iface["name"] in services,
            })
        free = [i["name"] for i in interfaces if self._free(i, routed, services)]
        pair_free = None
        if peer is not None:
            p_interfaces, p_routed, p_services = states[1]
            p_free = {i["name"] for i in p_interfaces if self._free(i, p_routed, p_services)}
            p_used = {naming.trunk_number(i["name"]) for i in p_interfaces if naming.is_trunk(i["name"])}
            pair_free = sorted((n for n in free if n in p_free), key=_port_key)
        else:
            p_used = set()
        used = {naming.trunk_number(i["name"]) for i in interfaces if naming.is_trunk(i["name"])} | p_used
        return {
            "device": name,
            "peer": peer["name"] if peer else None,
            "trunks": sorted(trunks, key=lambda t: t["number"]),
            "free_ports": sorted(free, key=_port_key),
            "pair_free_ports": pair_free,
            "next_number": next(n for n in range(1, MAX_NUMBER + 2) if n not in used),
        }

    # -- writing ---------------------------------------------------------------------------

    async def _tag(self) -> int:
        tag = await self.nb.find_one("extras/tags", slug=MLAG_TAG)
        if tag is None:
            tag = await self.nb.create("extras/tags", {"name": MLAG_TAG, "slug": MLAG_TAG, "color": "00756e"})
        return tag["id"]

    async def create(self, name: str, number: int, members: list[str], mlag: bool) -> dict:
        if not 1 <= number <= MAX_NUMBER:
            raise SegmentError(f"the trunk number must be 1-{MAX_NUMBER}")
        if not members:
            raise SegmentError("a trunk needs at least one member port")
        if len(set(members)) != len(members):
            raise SegmentError("a member port is listed twice")
        device = await self._device(name)
        trunk = naming.trunk(seed_ports.vendor_of_model((device.get("device_type") or {}).get("model")), number)
        devices = [device]
        if mlag:
            peer = await self._peer(device)
            if peer is None:
                raise SegmentError(f"{name} is not in an M-LAG pair; create a single trunk instead")
            devices.append(peer)
        for dev in devices:
            interfaces, routed, services = await self._state(dev)
            by_name = {i["name"]: i for i in interfaces}
            if trunk in by_name:
                raise SegmentError(f"{dev['name']} already has {trunk}")
            for member in members:
                iface = by_name.get(member)
                if iface is None:
                    raise SegmentError(f"{dev['name']} has no port {member!r}")
                if not self._free(iface, routed, services):
                    raise SegmentError(f"{dev['name']} {member} is not free: only front-panel ports in no trunk, "
                                       "with no address, segments or service join a trunk")
        tags = [await self._tag()] if mlag else []
        for dev in devices:
            lag = await self.nb.create("dcim/interfaces", {"device": dev["id"], "name": trunk, "type": "lag",
                                                           "tags": tags})
            for iface in await self.nb.find("dcim/interfaces", device_id=dev["id"]):
                if iface["name"] in members:
                    await self.nb.update("dcim/interfaces", iface["id"], {"lag": lag["id"]})
        return {"devices": [d["name"] for d in devices], "name": trunk, "members": sorted(members, key=_port_key),
                "kind": "m-lag" if mlag else "single"}

    async def delete(self, name: str, trunk: str) -> dict:
        """Delete a trunk (an M-LAG trunk on both devices of the pair). Refused while segments or
        a service use it, and for the peer-link."""
        from orchestrator.routing_intent import RoutingStore, status_of

        device = await self._device(name)
        found = await self.nb.find_one("dcim/interfaces", device_id=device["id"], name=trunk)
        if found is None or _kind(found) != "lag":
            raise SegmentError(f"{name} has no trunk {trunk!r}")
        if PEER_LINK_TAG in _tags(found):
            raise SegmentError(f"{trunk} is the M-LAG peer-link; it comes from the fabric YAML")
        devices = [device]
        if MLAG_TAG in _tags(found) and (peer := await self._peer(device)) is not None:
            devices.append(peer)
        plan = []
        for dev in devices:
            interfaces, _, services = await self._state(dev)
            lag = next((i for i in interfaces if i["name"] == trunk), None)
            if lag is None:
                continue
            if _cf(lag, "segments"):
                raise SegmentError(f"{dev['name']} {trunk} carries segments; detach them first")
            if trunk in services:
                raise SegmentError(f"{dev['name']} {trunk} is a service's port; delete the service first")
            members = [i for i in interfaces if _ref_id(i.get("lag")) == lag["id"]]
            plan.append((dev, lag, members))
        store = RoutingStore(self.nb)
        fabric = (device.get("site") or {}).get("slug")
        for dev, lag, members in plan:
            for member in members:
                await self.nb.update("dcim/interfaces", member["id"], {"lag": None})
            await self.nb.delete("dcim/interfaces", lag["id"])
            if status_of(dev) == "active":   # a planned device never had it deployed
                await store.retire_trunk(fabric, dev["name"], trunk, [m["name"] for m in members])
        return {"devices": [d["name"] for d, _, _ in plan], "name": trunk}


def _port_key(name: str) -> tuple:
    return tuple(int(n) if n.isdigit() else n for n in re.split(r"(\d+)", name))
