"""Where each switch is: NetBox site (the fabric), location (room or
hall), rack, U position and face. Written by the orchestrator only; not device config, so it
never shows in a diff.
"""

import asyncio
import re

from orchestrator.netbox import NetBoxClient
from orchestrator.segments import SegmentError

FACES = ("front", "rear")
SLUG = re.compile(r"[^a-z0-9]+")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._/-]{0,63}\Z")


def _slug(name: str) -> str:
    return SLUG.sub("-", name.lower()).strip("-")


def _id(value) -> int | None:
    return value.get("id") if isinstance(value, dict) else value


def _value(value):
    return value.get("value") if isinstance(value, dict) else value


def _position(value) -> int | None:
    return None if value is None else int(float(value))


class Locations:
    def __init__(self, netbox: NetBoxClient):
        self.nb = netbox

    async def _site(self, fabric: str) -> dict:
        site = await self.nb.find_one("dcim/sites", slug=fabric)
        if site is None:
            raise SegmentError(f"no fabric {fabric!r}")
        return site

    async def _height(self, device: dict) -> int:
        """Rack units the device takes (its NetBox device type, 1 if unset)."""
        types = await self.nb.find("dcim/device-types", id=_id(device.get("device_type")))
        return int(float((types[0] if types else {}).get("u_height") or 1))

    async def racks(self, fabric: str) -> dict:
        """The fabric's locations and racks, with the devices in each rack."""
        site = await self._site(fabric)
        locations, racks, devices = await asyncio.gather(
            self.nb.find("dcim/locations", site_id=site["id"]), self.nb.find("dcim/racks", site_id=site["id"]),
            self.nb.find("dcim/devices", site_id=site["id"]))
        racked = [d for d in devices if _id(d.get("rack")) is not None]
        heights = dict(zip((d["id"] for d in racked), await asyncio.gather(*(self._height(d) for d in racked))))
        result = []
        for location in sorted(locations, key=lambda l: l["name"]):
            entries = []
            for rack in sorted((r for r in racks if _id(r.get("location")) == location["id"]), key=lambda r: r["name"]):
                inside = [{"name": d["name"], "position": _position(d.get("position")), "face": _value(d.get("face")) or None,
                           "u_height": heights[d["id"]]}
                          for d in devices if _id(d.get("rack")) == rack["id"]]
                entries.append({"name": rack["name"], "u_height": int(rack.get("u_height") or 42),
                                "devices": sorted(inside, key=lambda d: (-(d["position"] or 0), d["name"]))})
            result.append({"name": location["name"], "racks": entries})
        return {"fabric": fabric, "locations": result}

    async def _rack(self, site: dict, location: str, rack: str) -> tuple[dict | None, dict | None]:
        loc = next((l for l in await self.nb.find("dcim/locations", site_id=site["id"]) if l["name"] == location), None)
        if loc is None:
            return None, None
        found = next((r for r in await self.nb.find("dcim/racks", site_id=site["id"])
                      if r["name"] == rack and _id(r.get("location")) == loc["id"]), None)
        return loc, found

    async def add_rack(self, fabric: str, location: str, rack: str, u_height: int = 42) -> dict:
        """A rack in a location of the fabric; the location is created with its first rack."""
        for label, value in (("location", location), ("rack", rack)):
            if not NAME.match(value or ""):
                raise SegmentError(f"a {label} name is 1-64 letters, digits, spaces or . _ / -")
        if not 1 <= u_height <= 60:
            raise SegmentError("a rack is 1-60 U high")
        site = await self._site(fabric)
        loc, found = await self._rack(site, location, rack)
        if found is not None:
            raise SegmentError(f"{location} already has rack {rack}")
        if loc is None:
            loc = await self.nb.create("dcim/locations", {"site": site["id"], "name": location, "slug": _slug(location)})
        await self.nb.create("dcim/racks", {"site": site["id"], "location": loc["id"], "name": rack,
                                            "u_height": u_height, "status": "active"})
        return {"location": location, "rack": rack, "u_height": u_height}

    async def delete_rack(self, fabric: str, location: str, rack: str) -> None:
        """Delete an empty rack, and its location when no rack is left in it."""
        site = await self._site(fabric)
        loc, found = await self._rack(site, location, rack)
        if found is None:
            raise SegmentError(f"{fabric} has no rack {location}/{rack}")
        used = [d["name"] for d in await self.nb.find("dcim/devices", site_id=site["id"]) if _id(d.get("rack")) == found["id"]]
        if used:
            raise SegmentError(f"rack {location}/{rack} holds {', '.join(sorted(used))}; move them first")
        await self.nb.delete("dcim/racks", found["id"])
        if not [r for r in await self.nb.find("dcim/racks", site_id=site["id"]) if _id(r.get("location")) == loc["id"]]:
            await self.nb.delete("dcim/locations", loc["id"])

    async def place(self, name: str, location: str | None, rack: str | None, position: int | None,
                    face: str | None) -> dict:
        """Put a device in a rack (U position and face), or take it out (no location and rack)."""
        device = await self.nb.find_one("dcim/devices", name=name)
        if device is None:
            raise SegmentError(f"no device {name!r}")
        if not location and not rack:
            await self.nb.update("dcim/devices", device["id"], {"rack": None, "location": None, "position": None,
                                                                "face": ""})
            return {"device": name, "location": None, "rack": None, "position": None, "face": None}
        site = await self.nb.find_one("dcim/sites", id=_id(device.get("site")))
        loc, found = await self._rack(site, location or "", rack or "")
        if found is None:
            raise SegmentError(f"{site['slug']} has no rack {location}/{rack}")
        if position is not None:
            if face not in FACES:
                raise SegmentError("a device with a U position needs a face: front or rear")
            height, top = await self._height(device), int(found.get("u_height") or 42)
            if not 1 <= position <= top - height + 1:
                raise SegmentError(f"U position must be 1-{top - height + 1} in a {top} U rack")
            mine = set(range(position, position + height))
            for other in await self.nb.find("dcim/devices", site_id=site["id"]):
                if other["id"] == device["id"] or _id(other.get("rack")) != found["id"] or other.get("position") is None:
                    continue
                start = _position(other["position"])
                if mine & set(range(start, start + await self._height(other))):
                    raise SegmentError(f"U{position} of {location}/{rack} is taken by {other['name']}")
        await self.nb.update("dcim/devices", device["id"], {
            "location": loc["id"], "rack": found["id"], "position": position, "face": face if position is not None else "",
        })
        return {"device": name, "location": location, "rack": rack, "position": position,
                "face": face if position is not None else None}


def placement(device: dict) -> dict:
    """A device's location as the device list shows it (NetBox's brief rack and location)."""
    rack, location = device.get("rack"), device.get("location")
    return {
        "location": location.get("name") if isinstance(location, dict) else None,
        "rack": rack.get("name") if isinstance(rack, dict) else None,
        "position": _position(device.get("position")),
        "face": _value(device.get("face")) or None,
    }
