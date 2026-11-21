"""In-memory stand-in for the NetBoxClient methods the seed uses: the tests' NetBox, and the
import preview's (a fabric is built here first, nothing is written to the real NetBox).

It mirrors the NetBox 4.4 REST shapes the seed relies on: foreign keys come back
as {"id": ...}, choice fields as {"value": ...}, and "next available" allocation
skips used addresses (and network/broadcast outside /31-/32).
"""

import copy
import ipaddress
import itertools

from orchestrator.netbox import NetBoxError

# Foreign-key fields, returned as nested {"id": ..., plus name/slug like NetBox's brief objects}.
_FK = {"role", "device_type", "manufacturer", "site", "device", "lag", "rir", "cable", "primary_ip4", "vrf", "rack",
       "location"}
_FK_ENDPOINT = {
    "role": "dcim/device-roles",
    "device_type": "dcim/device-types",
    "manufacturer": "dcim/manufacturers",
    "site": "dcim/sites",
    "device": "dcim/devices",
    "lag": "dcim/interfaces",
    "rir": "ipam/rirs",
    "vrf": "ipam/vrfs",
    "rack": "dcim/racks",
    "location": "dcim/locations",
}
_FK_LISTS = {"tags", "asns", "sites"}
_CHOICES = {"status", "type", "face"}
# Fields that must be unique per endpoint (tuples of field names).
_UNIQUE = {
    "dcim/devices": [("site", "name")],
    "dcim/interfaces": [("device", "name")],
    "dcim/interface-templates": [("device_type", "name")],
    "dcim/device-roles": [("slug",)],
    "dcim/device-types": [("manufacturer", "model"), ("manufacturer", "slug")],
    "dcim/manufacturers": [("slug",)],
    "dcim/sites": [("slug",)],
    "extras/custom-fields": [("name",)],
    "extras/tags": [("slug",)],
    "ipam/asns": [("asn",)],
    "ipam/rirs": [("slug",)],
    "dcim/locations": [("site", "slug"), ("site", "name")],
    "dcim/racks": [("location", "name")],
}
_NONUNIQUE_IP_ROLES = {"anycast", "vip", "vrrp", "hsrp", "glbp", "carp"}


class FakeNetBox:
    def __init__(self):
        self.objects: dict[str, dict[int, dict]] = {}
        self._next_id = itertools.count(1)
        self.writes = 0

    # -- NetBoxClient interface ------------------------------------------------

    async def find(self, endpoint: str, **filters) -> list[dict]:
        return [self._render(endpoint, o) for o in self._store(endpoint).values() if self._match(o, filters)]

    async def find_one(self, endpoint: str, **filters) -> dict | None:
        found = await self.find(endpoint, **filters)
        if len(found) > 1:
            raise NetBoxError("more than one")
        return found[0] if found else None

    async def create(self, endpoint: str, data: dict) -> dict:
        self.writes += 1
        obj = copy.deepcopy(data)
        obj["id"] = next(self._next_id)
        if endpoint == "dcim/cables":
            return self._create_cable(obj)
        if endpoint == "ipam/ip-addresses":
            self._check_ip_unique(obj)
        self._check_unique(endpoint, obj)
        obj.setdefault("custom_fields", {})
        self._store(endpoint)[obj["id"]] = obj
        return self._render(endpoint, obj)

    async def update(self, endpoint: str, object_id: int, data: dict) -> dict:
        self.writes += 1
        obj = self._store(endpoint)[object_id]
        updated = {**obj, **copy.deepcopy(data)}
        if "custom_fields" in data:
            updated["custom_fields"] = {**obj.get("custom_fields", {}), **data["custom_fields"]}
        if endpoint == "ipam/ip-addresses":
            self._check_ip_unique(updated)
        self._store(endpoint)[object_id] = updated
        return self._render(endpoint, updated)

    async def delete(self, endpoint: str, object_id: int) -> None:
        self.writes += 1
        del self._store(endpoint)[object_id]
        # NetBox's cascades: a cable frees its ports; a device takes its interfaces, an interface
        # its addresses.
        if endpoint == "dcim/cables":
            for iface in self._store("dcim/interfaces").values():
                if iface.get("cable") == object_id:
                    iface["cable"] = None
        if endpoint == "dcim/devices":
            for iface_id in [i["id"] for i in self._store("dcim/interfaces").values() if i["device"] == object_id]:
                await self.delete("dcim/interfaces", iface_id)
        if endpoint == "dcim/interfaces":
            for ip_id in [ip["id"] for ip in self._store("ipam/ip-addresses").values()
                          if ip.get("assigned_object_id") == object_id]:
                del self._store("ipam/ip-addresses")[ip_id]

    async def allocate_ip(self, prefix_id: int, data: dict) -> dict:
        # Like NetBox: only IPs in the parent's VRF count, and the new IP gets that VRF.
        parent_obj = self._store("ipam/prefixes")[prefix_id]
        parent = ipaddress.ip_network(parent_obj["prefix"])
        vrf = parent_obj.get("vrf")
        used = {ipaddress.ip_interface(ip["address"]).ip for ip in self._store("ipam/ip-addresses").values()
                if ip.get("vrf") == vrf}
        # IPv6: never the subnet-router anycast address (the prefix's own, ::0), as Python's hosts().
        hosts = parent.hosts() if parent.prefixlen < 31 or (parent.version == 6 and parent.prefixlen < 127) else iter(parent)
        for host in hosts:
            if host not in used:
                return await self.create("ipam/ip-addresses",
                                         {**data, "address": f"{host}/{parent.prefixlen}", "vrf": vrf})
        raise NetBoxError("prefix full")

    async def allocate_prefix(self, prefix_id: int, data: dict) -> dict:
        parent_obj = self._store("ipam/prefixes")[prefix_id]
        parent = ipaddress.ip_network(parent_obj["prefix"])
        vrf = parent_obj.get("vrf")
        taken = [ipaddress.ip_network(p["prefix"]) for p in self._store("ipam/prefixes").values()
                 if p.get("vrf") == vrf]
        for candidate in parent.subnets(new_prefix=data["prefix_length"]):
            # Same family only: IPv4 and IPv6 prefixes share a VRF and never overlap.
            if not any(candidate.overlaps(t) for t in taken
                       if t.version == parent.version and t != parent and t.subnet_of(parent)):
                body = {k: v for k, v in data.items() if k != "prefix_length"}
                return await self.create("ipam/prefixes", {**body, "prefix": str(candidate), "vrf": vrf})
        raise NetBoxError("prefix full")

    # -- internals -------------------------------------------------------------

    def _store(self, endpoint: str) -> dict[int, dict]:
        return self.objects.setdefault(endpoint, {})

    def _create_cable(self, obj: dict) -> dict:
        interfaces = self._store("dcim/interfaces")
        ends = [t["object_id"] for t in obj["a_terminations"] + obj["b_terminations"]]
        for iface_id in ends:
            if interfaces[iface_id].get("cable"):
                raise NetBoxError(f"interface {iface_id} already has a cable")
        for iface_id in ends:
            interfaces[iface_id]["cable"] = obj["id"]
        self._store("dcim/cables")[obj["id"]] = obj
        return obj

    def _brief(self, key: str, value: int) -> dict:
        target = self._store(_FK_ENDPOINT[key]).get(value, {}) if key in _FK_ENDPOINT else {}
        return {"id": value, **{k: target[k] for k in ("name", "slug", "model") if k in target}}

    def _site_slug(self, obj: dict) -> str | None:
        site_id = obj.get("site")
        if site_id is None and obj.get("device") is not None:
            site_id = self._store("dcim/devices").get(obj["device"], {}).get("site")
        return self._store("dcim/sites").get(site_id, {}).get("slug")

    def _check_unique(self, endpoint: str, obj: dict) -> None:
        for fields in _UNIQUE.get(endpoint, []):
            for other in self._store(endpoint).values():
                if all(other.get(f) == obj.get(f) for f in fields):
                    raise NetBoxError(f"{endpoint}: duplicate {fields}")

    def _check_ip_unique(self, obj: dict) -> None:
        ip = ipaddress.ip_interface(obj["address"]).ip
        for other in self._store("ipam/ip-addresses").values():
            if other["id"] == obj["id"] or ipaddress.ip_interface(other["address"]).ip != ip:
                continue
            if other.get("vrf") != obj.get("vrf"):   # uniqueness is per VRF
                continue
            if obj.get("role") in _NONUNIQUE_IP_ROLES and other.get("role") in _NONUNIQUE_IP_ROLES:
                continue
            raise NetBoxError(f"duplicate IP address {ip}")

    def _match(self, obj: dict, filters: dict) -> bool:
        for key, want in filters.items():
            if key == "device_id" and "assigned_object_id" in obj:
                iface = self._store("dcim/interfaces").get(obj["assigned_object_id"], {})
                if iface.get("device") != want:
                    return False
            elif key == "interface_id":
                if not (obj.get("assigned_object_type") == "dcim.interface" and obj.get("assigned_object_id") == want):
                    return False
            elif key == "site":
                if self._site_slug(obj) != want:
                    return False
            elif key == "role" and isinstance(obj.get("role"), int):
                # Like NetBox: a device's role is filtered by its slug.
                if self._store("dcim/device-roles").get(obj["role"], {}).get("slug") != want:
                    return False
            elif key == "cabled":
                if bool(obj.get("cable")) != (str(want).lower() == "true"):
                    return False
            elif key == "address" and "/" not in str(want):
                # Like NetBox: an address without a mask matches it with any mask.
                if str(ipaddress.ip_interface(obj["address"]).ip) != str(want):
                    return False
            elif key == "has_primary_ip":
                if bool(obj.get("primary_ip4")) != (str(want).lower() == "true"):
                    return False
            elif key == "parent":
                if not ipaddress.ip_interface(obj["address"]).ip in ipaddress.ip_network(want):
                    return False
            elif key == "within":
                if not ipaddress.ip_network(obj["prefix"]).subnet_of(ipaddress.ip_network(want)) or obj["prefix"] == want:
                    return False
            elif key.endswith("_id"):
                if obj.get(key[:-3]) != want:
                    return False
            elif obj.get(key) != want:
                return False
        return True

    def _render(self, endpoint: str, obj: dict) -> dict:
        out = copy.deepcopy(obj)
        for key, value in obj.items():
            if key in _FK and value is not None:
                out[key] = self._brief(key, value)
            elif key == "tags":
                tags = self._store("extras/tags")
                out[key] = [{"id": v, **{k: tags.get(v, {}).get(k) for k in ("name", "slug")}} for v in value]
            elif key in _FK_LISTS:
                out[key] = [{"id": v} for v in value]
            elif key in _CHOICES and isinstance(value, str):
                out[key] = {"value": value, "label": value}
        out.setdefault("vrf", None)
        if endpoint == "dcim/interfaces":
            out.setdefault("cable", None)
            if obj.get("cable"):
                peers = [
                    i for i in self._store("dcim/interfaces").values()
                    if i.get("cable") == obj["cable"] and i["id"] != obj["id"]
                ]
                out["link_peers_type"] = "dcim.interface"
                out["link_peers"] = [
                    {"id": i["id"], "name": i["name"], "device": self._brief("device", i["device"])} for i in peers
                ]
            else:
                out["link_peers_type"] = None
                out["link_peers"] = []
        if endpoint == "dcim/devices":
            # Nested IPs carry their address (an IPAddress brief field in NetBox).
            if obj.get("primary_ip4"):
                ip = self._store("ipam/ip-addresses")[obj["primary_ip4"]]
                out["primary_ip4"] = {"id": ip["id"], "address": ip["address"]}
            out["custom_fields"] = {
                k: ({"id": v} if k == "mlag_peer" and v is not None else v)
                for k, v in obj.get("custom_fields", {}).items()
            }
        return out
