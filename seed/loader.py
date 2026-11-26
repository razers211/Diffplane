"""Load a fabric definition into NetBox, idempotently.

Every step looks up the object first and only creates or updates it when NetBox
differs from the definition. Nothing is deleted. Addresses and point-to-point
prefixes are always allocated with NetBox's "next available" endpoints.
"""

import ipaddress
from dataclasses import dataclass, field

from orchestrator import documents
from orchestrator.netbox import NetBoxClient
from seed.model import Fabric, LeafPair, Link, interface_type
from seed import ports
from generators import naming

ROLES = {"spine": "2196f3", "leaf": "4caf50", "border-leaf": "ff9800"}
PEER_LINK_TAG = "peer-link"
MLAG_TAG = "m-lag"   # an Eth-Trunk bound to the pair's M-LAG
UNNUMBERED_TAG = "unnumbered"   # a fabric link port borrowing LoopBack0's address
MANUFACTURER = "Huawei"


class SeedError(Exception):
    pass


@dataclass
class Report:
    changes: list[str] = field(default_factory=list)

    def add(self, change: str) -> None:
        self.changes.append(change)


def _matches(current, want) -> bool:
    """Compare a value read from NetBox with the value we would write."""
    if isinstance(want, dict):
        return isinstance(current, dict) and all(_matches(current.get(k), v) for k, v in want.items())
    if isinstance(want, list):
        if not isinstance(current, list) or len(current) != len(want):
            return False
        return sorted(_ref(c) for c in current) == sorted(want)
    if isinstance(current, dict):
        # Nested object ({"id": ...}) or choice ({"value": ...}).
        return _ref(current) == want
    return current == want


def _ref(value):
    if isinstance(value, dict):
        return value["value"] if "value" in value else value.get("id")
    return value


class Seeder:
    def __init__(self, netbox: NetBoxClient, fabric: Fabric):
        self.nb = netbox
        self.fabric = fabric
        self.report = Report()
        self._ids: dict[str, int] = {}

    async def run(self) -> Report:
        await self._schema()
        await ports.refresh(self.nb)   # layouts learned from switches
        await self._site()
        await self._devices()
        for pair in self.fabric.mlag_pairs:   # leaf pairs and a spine gateway pair
            await self._pair(pair)
        for link in self.fabric.links:
            await self._spine_link(link)
        await self._loopbacks()
        return self.report

    # -- generic helpers ---------------------------------------------------

    async def _ensure(
        self, endpoint: str, lookup: dict, desired: dict, label: str, pick=None, exact=frozenset(), on_create=None
    ) -> dict:
        found = [o for o in await self.nb.find(endpoint, **lookup) if pick is None or pick(o)]
        if len(found) > 1:
            raise SeedError(f"more than one {endpoint} matches {label}")
        obj = found[0] if found else None
        if obj is None:
            # `on_create`: fields set only on a new object (owned elsewhere afterwards).
            obj = await self.nb.create(endpoint, {**desired, **(on_create or {})})
            self.report.add(f"create {endpoint} {label}")
            return obj
        # Keys in `exact` are compared whole (a JSON blob), not as a subset.
        diff = {
            k: v for k, v in desired.items()
            if not (obj.get(k) == v if k in exact else _matches(obj.get(k), v))
        }
        if diff:
            obj = await self.nb.update(endpoint, obj["id"], diff)
            self.report.add(f"update {endpoint} {label}: {', '.join(sorted(diff))}")
        return obj

    async def _interface(self, device: str, name: str, **extra) -> dict:
        return await self._ensure(
            "dcim/interfaces",
            {"device_id": self._ids[device], "name": name},
            {"device": self._ids[device], "name": name, **extra},
            f"{device} {name}",
            # A front-panel port already has its model's type (copper or fibre, or as read from the switch).
            on_create={"type": interface_type(name)},
        )

    # -- schema: custom fields, tags, roles ----------------------------------

    async def _schema(self) -> None:
        await self._ensure(
            "extras/custom-fields",
            {"name": "mlag_peer"},
            {
                "name": "mlag_peer",
                "label": "M-LAG peer",
                "type": "object",
                "object_types": ["dcim.device"],
                "related_object_type": "dcim.device",
                "description": "The other leaf of this device's M-LAG pair",
            },
            "mlag_peer",
        )
        await self._ensure(
            "extras/custom-fields",
            {"name": "bd_id"},
            {
                "name": "bd_id",
                "label": "Bridge domain ID",
                "type": "integer",
                "object_types": ["vpn.l2vpn"],
                "description": "Bridge-domain ID of the segment",
            },
            "bd_id",
        )
        await self._ensure(
            "extras/custom-fields",
            {"name": "fabric"},
            {
                "name": "fabric",
                "label": "Fabric",
                "type": "object",
                "object_types": ["vpn.l2vpn", "ipam.vrf"],
                "related_object_type": "dcim.site",
                "description": "The fabric (site) a segment or firewall service belongs to",
            },
            "fabric",
        )
        for name, kind, types, related, description in [
            ("gateway_prefix", "object", ["vpn.l2vpn"], "ipam.prefix", "Gateway subnet of a routed segment"),
            ("peers", "json", ["vpn.l2vpn"], None, "VNIs of the segments this one routes to directly"),
            ("firewall", "text", ["vpn.l2vpn"], None, "Firewall service that holds the segment's default route"),
            ("gateway_prefix6", "object", ["vpn.l2vpn"], "ipam.prefix", "IPv6 gateway /64 of a dual-stack segment"),
            ("ipv6", "json", ["vpn.l2vpn"], None,
             'IPv6 of a dual-stack segment: {"hosts": "slaac"|"dhcpv6"|"none", "servers": [DHCPv6 servers]}'),
            ("dhcp_relay", "json", ["vpn.l2vpn"], None,
             'DHCP relay of a routed segment: {"servers": [{"address", "service" or "segment"}], "loopback": n}'),
            ("service_device", "object", ["ipam.vrf"], "dcim.device", "Leaf the firewall is cabled to"),
            ("service_port", "text", ["ipam.vrf"], None, "Port of that leaf towards the firewall"),
            ("service_vlan", "integer", ["ipam.vrf"], None, "VLAN of the Vlanif towards the firewall"),
            ("service_address", "text", ["ipam.vrf"], None, "Vlanif address with prefix length"),
            ("service_next_hop", "text", ["ipam.vrf"], None, "Firewall address: next hop of default routes"),
            ("service_asn", "integer", ["ipam.vrf"], None, "Firewall ASN for the eBGP session in the service instance"),
            ("service_kind", "text", ["ipam.vrf"], None, "firewall (default) or isp: an ISP's session is a routing-intent peer"),
            ("service_addresses", "json", ["ipam.vrf"], None,
             "Service on an M-LAG pair: each leaf's main Vlanif address (service_address is the shared M-LAG address)"),
            ("service_ipv6", "json", ["ipam.vrf"], None, "IPv6 of a service: Vlanif address and next hop"),
        ]:
            await self._ensure(
                "extras/custom-fields",
                {"name": name},
                {"name": name, "label": name.replace("_", " ").capitalize(), "type": kind, "object_types": types,
                 **({"related_object_type": related} if related else {}), "description": description},
                name,
            )
        await self._ensure(
            "extras/custom-fields",
            {"name": "segments"},
            {
                "name": "segments",
                "label": "Segments",
                "type": "json",
                "object_types": ["dcim.interface"],
                "description": 'Segments this port carries: [{"vni": N, "vid": X or null (untagged)}]',
            },
            "segments",
        )
        await self._ensure(
            "extras/custom-fields",
            {"name": "ssh_host_keys"},
            {
                "name": "ssh_host_keys",
                "label": "SSH host keys",
                "type": "json",
                "object_types": ["dcim.device"],
                # Written only by `python -m orchestrator.hostkeys pin` (orchestrator/hostkeys.py).
                "description": 'Pinned host keys: {"22": ["<type> <base64>"], "830": [...]}; '
                               "set with python -m orchestrator.hostkeys pin",
            },
            "ssh_host_keys",
        )
        await self._ensure(
            "extras/custom-fields",
            {"name": "ztp"},
            {
                "name": "ztp",
                "label": "ZTP registration",
                "type": "json",
                "object_types": ["dcim.device"],
                # Written only by the orchestrator (orchestrator/ztp.py).
                "description": "Zero-touch provisioning of a planned switch: MEth MAC, management address, "
                               "gateway, and the host keys it reported",
            },
            "ztp",
        )
        tag = await self._ensure(
            "extras/tags",
            {"slug": PEER_LINK_TAG},
            {"name": PEER_LINK_TAG, "slug": PEER_LINK_TAG, "color": "9e9e9e"},
            PEER_LINK_TAG,
        )
        self._ids["tag:peer-link"] = tag["id"]
        tag = await self._ensure(
            "extras/tags",
            {"slug": MLAG_TAG},
            {"name": MLAG_TAG, "slug": MLAG_TAG, "color": "00756e"},
            MLAG_TAG,
        )
        self._ids["tag:m-lag"] = tag["id"]
        tag = await self._ensure(
            "extras/tags",
            {"slug": UNNUMBERED_TAG},
            {"name": UNNUMBERED_TAG, "slug": UNNUMBERED_TAG, "color": "9e9e9e"},
            UNNUMBERED_TAG,
        )
        self._ids["tag:unnumbered"] = tag["id"]
        for role, color in ROLES.items():
            obj = await self._ensure(
                "dcim/device-roles", {"slug": role}, {"name": role, "slug": role, "color": color}, role
            )
            self._ids[f"role:{role}"] = obj["id"]

    # -- site, ASN, prefix pools -----------------------------------------------

    async def _site(self) -> None:
        f = self.fabric
        rir = await self._ensure(
            "ipam/rirs", {"slug": "private"}, {"name": "Private", "slug": "private", "is_private": True}, "private"
        )
        asn = await self._ensure("ipam/asns", {"asn": f.asn}, {"asn": f.asn, "rir": rir["id"]}, str(f.asn))
        site = await self._ensure(
            "dcim/sites",
            {"slug": f.slug},
            {"name": f.name, "slug": f.slug, "status": "active", "asns": [asn["id"]]},
            f.name,
        )
        self._ids["site"] = site["id"]
        data = _with_asn(f.services.config_context(), f.asn, f.loopback_prefix, f.pip_prefix)
        existing = await documents.find(self.nb, f"fabric-{f.slug}")
        if existing is not None:
            # NTP, SNMP and the gateway MAC are set in the GUI
            # once the fabric exists: the YAML's values only seed a new fabric, re-seeding keeps the GUI's.
            current = ((existing.get("data") or {}).get("fabric") or {})
            for key in GUI_KEYS:
                data["fabric"].pop(key, None)
                if key in current:
                    data["fabric"][key] = current[key]
            # A centralized fabric's gateway pair can be moved to a spine pair added in the GUI
            #; re-seeding keeps it while the fabric stays centralized.
            if current.get("architecture") == data["fabric"].get("architecture") == "centralized" \
                    and current.get("gateway_pair"):
                data["fabric"]["gateway_pair"] = current["gateway_pair"]
        await self._ensure(
            documents.ENDPOINT,
            {"name": f"fabric-{f.slug}"},
            {
                "name": f"fabric-{f.slug}",   # description and is_active: the intent store's, on publishing
                "sites": [site["id"]],
                "data": data,
            },
            f"fabric-{f.slug}",
            exact={"data"},
        )
        vrf = await self._ensure(
            "ipam/vrfs",
            {"name": f"fabric-{f.slug}"},
            {"name": f"fabric-{f.slug}", "enforce_unique": True,
             "description": f"{f.name} fabric addressing (IPAM namespace, not a device VPN instance)"},
            f"fabric-{f.slug}",
        )
        self._ids["vrf"] = vrf["id"]
        prefixes = {"loopback": f.loopback_prefix, "vtep": f.vtep_prefix, "p2p": f.p2p_prefix,
                    "mlag_bypass": f.mlag_bypass_prefix, "gateways": f.gateways_prefix, "pip": f.pip_prefix}
        pools = {key: (prefix, *POOLS[key]) for key, prefix in prefixes.items() if prefix is not None}
        for key, (prefix, status, purpose) in pools.items():
            await self._adopt(prefix)
            obj = await self._ensure(
                "ipam/prefixes",
                {"prefix": str(prefix), "vrf_id": vrf["id"]},
                {
                    "prefix": str(prefix),
                    "vrf": vrf["id"],
                    "status": status,
                    "scope_type": "dcim.site",
                    "scope_id": site["id"],
                    "description": f"{f.name} {purpose}",
                },
                str(prefix),
            )
            self._ids[f"prefix:{key}"] = obj["id"]

    async def _adopt(self, pool) -> None:
        """Move this fabric's pool, its child prefixes and IPs from the global table into
        the fabric VRF (data seeded before fabrics had their own IPAM namespace)."""
        site, vrf = self._ids["site"], self._ids["vrf"]
        legacy = [p for p in await self.nb.find("ipam/prefixes", prefix=str(pool))
                  if _global(p) and p.get("scope_id") == site]
        if not legacy:
            return
        children = [p for p in await self.nb.find("ipam/prefixes", within=str(pool)) if _global(p)]
        for prefix in legacy + children:
            await self.nb.update("ipam/prefixes", prefix["id"], {"vrf": vrf})
        moved = 0
        for ip in await self.nb.find("ipam/ip-addresses", parent=str(pool)):
            if ip.get("vrf") is None:
                await self.nb.update("ipam/ip-addresses", ip["id"], {"vrf": vrf})
                moved += 1
        self.report.add(f"move {pool} ({len(children)} child prefixes, {moved} IPs) into fabric-{self.fabric.slug}")

    # -- devices ---------------------------------------------------------------

    async def _devices(self) -> None:
        devices = [(s.name, s.model, "spine") for s in self.fabric.spines]
        devices += [(leaf, p.model, p.role) for p in self.fabric.leaf_pairs for leaf in p.leaves]
        for model in sorted({model for _, model, _ in devices}):
            await self._device_type(model)
        for name, model, role in devices:
            await self._device(name, model, role)

    async def _device_type(self, model: str) -> None:
        maker = ports.manufacturer(model)   # Annet picks the vendor (breed) from it
        manufacturer = await self._ensure(
            "dcim/manufacturers",
            {"slug": maker.lower()},
            {"name": maker, "slug": maker.lower()},
            maker,
        )
        obj = await self._ensure(
            "dcim/device-types",
            {"manufacturer_id": manufacturer["id"], "model": model},
            {"manufacturer": manufacturer["id"], "model": model, "slug": model.lower()},
            model,
        )
        self._ids[f"type:{model}"] = obj["id"]

    async def _device(self, name: str, model: str, role: str) -> None:
        """One device of the fabric with every front-panel port of its model."""
        elsewhere = [d for d in await self.nb.find("dcim/devices", name=name)
                     if (d.get("site") or {}).get("id") != self._ids["site"]]
        if elsewhere:
            raise SeedError(f"device name {name!r} is already used by another fabric; names are global")
        obj = await self._ensure(
            "dcim/devices",
            {"site_id": self._ids["site"], "name": name},
            {
                "name": name,
                "site": self._ids["site"],
                "device_type": self._ids[f"type:{model}"],
            },
            name,
            # A new device is modelled only; the GUI makes it active with its management
            # address and owns both from the start. Its role is the GUI's
            # afterwards too (leaf <-> border-leaf).
            on_create={"status": "planned", "role": self._ids[f"role:{role}"]},
        )
        self._ids[name] = obj["id"]
        await self._front_panel(name, model)

    async def _front_panel(self, device: str, model: str) -> None:
        """Every port of the model exists in NetBox (one lookup per device)."""
        wanted = ports.layout(model)
        if not wanted:
            return
        existing = {i["name"] for i in await self.nb.find("dcim/interfaces", device_id=self._ids[device])}
        missing = [p for p in wanted if p not in existing]
        for port in missing:
            await self.nb.create("dcim/interfaces", {"device": self._ids[device], "name": port, "type": wanted[port]})
        if missing:
            self.report.add(f"create dcim/interfaces {device}: {len(missing)} front-panel ports")

    # -- M-LAG pair --------------------------------------------------------------

    async def _pair(self, pair: LeafPair) -> None:
        a, b = pair.leaves
        # The YAML's server trunks are created only when the pair is first seeded; afterwards
        # the GUI owns them, so a trunk deleted there does not come back.
        first = True
        for leaf in pair.leaves:
            for device in await self.nb.find("dcim/devices", site_id=self._ids["site"], name=leaf):
                if (device.get("custom_fields") or {}).get("mlag_peer"):
                    first = False
        for leaf, peer in ((a, b), (b, a)):
            await self._ensure(
                "dcim/devices",
                {"site_id": self._ids["site"], "name": leaf},
                {"custom_fields": {"mlag_peer": self._ids[peer]}},
                f"{leaf} mlag_peer",
            )
            trunk = await self._interface(leaf, pair.peer_link_name, tags=[self._ids["tag:peer-link"]])
            for member in pair.peer_link_members:
                await self._interface(leaf, member, lag=trunk["id"])
        for member in pair.peer_link_members:
            await self._cable(a, member, b, member)
        for trunk in pair.mlag_trunks:
            for leaf in pair.leaves:
                if first:
                    lag = await self._interface(leaf, trunk.name, tags=[self._ids["tag:m-lag"]])
                    for member in trunk.members:
                        await self._interface(leaf, member, lag=lag["id"])
                    continue
                # Existing pair: only mark trunks made before the tag existed (lab Eth-Trunk1).
                for lag in await self.nb.find("dcim/interfaces", device_id=self._ids[leaf], name=trunk.name):
                    if not any(t.get("slug") == MLAG_TAG for t in lag.get("tags") or []):
                        await self.nb.update("dcim/interfaces", lag["id"],
                                             {"tags": [*(t["id"] for t in lag.get("tags") or []), self._ids["tag:m-lag"]]})
                        self.report.add(f"update dcim/interfaces {leaf} {trunk.name}: tags")
        # VXLAN bypass between the peers: a Vlanif over the peer-link, one /30 per pair.
        vlanif = naming.svi(self.fabric.vendor, self.fabric.services.mlag_bypass_vlan)
        for leaf in pair.leaves:
            await self._rename_old_bypass(leaf, vlanif)
        ifaces = [await self._interface(leaf, vlanif) for leaf in pair.leaves]
        await self._link_addresses(
            [(a, vlanif), (b, vlanif)], ifaces, "mlag_bypass", self.fabric.mlag_bypass_prefix, f"{a}-{b} M-LAG bypass"
        )

    async def _rename_old_bypass(self, leaf: str, vlanif: str) -> None:
        """If the bypass VLAN changed, rename the old bypass Vlanif (keeping its address)."""
        pool = self.fabric.mlag_bypass_prefix
        for iface in await self.nb.find("dcim/interfaces", device_id=self._ids[leaf]):
            name = iface["name"]
            if name == vlanif or naming.svi_vlan(name) is None:
                continue
            ips = await self.nb.find("ipam/ip-addresses", interface_id=iface["id"])
            if any(ipaddress.ip_interface(ip["address"]).ip in pool for ip in ips):
                await self.nb.update("dcim/interfaces", iface["id"], {"name": vlanif})
                self.report.add(f"update dcim/interfaces {leaf} {name}: name -> {vlanif}")

    # -- cables and point-to-point links ------------------------------------------

    async def _cable(self, dev_a: str, port_a: str, dev_b: str, port_b: str) -> tuple[dict, dict]:
        iface_a = await self._interface(dev_a, port_a)
        iface_b = await self._interface(dev_b, port_b)
        cable_a, cable_b = _ref(iface_a.get("cable")), _ref(iface_b.get("cable"))
        label = f"{dev_a} {port_a} <> {dev_b} {port_b}"
        if cable_a is None and cable_b is None:
            await self.nb.create(
                "dcim/cables",
                {
                    "a_terminations": [{"object_type": "dcim.interface", "object_id": iface_a["id"]}],
                    "b_terminations": [{"object_type": "dcim.interface", "object_id": iface_b["id"]}],
                    "status": "connected",
                },
            )
            self.report.add(f"create dcim/cables {label}")
        elif cable_a != cable_b:
            raise SeedError(f"{label}: an existing cable connects these ports elsewhere; fix it in NetBox first")
        return iface_a, iface_b

    async def _spine_link(self, link: Link) -> None:
        ifaces = await self._cable(link.spine, link.spine_port, link.leaf, link.leaf_port)
        label = f"{link.spine} {link.spine_port} - {link.leaf} {link.leaf_port}"
        ends = [(link.spine, link.spine_port), (link.leaf, link.leaf_port)]
        if link.unnumbered:
            for iface in ifaces:
                ids = [t["id"] for t in iface.get("tags") or []]
                if self._ids["tag:unnumbered"] not in ids:
                    await self.nb.update("dcim/interfaces", iface["id"], {"tags": [*ids, self._ids["tag:unnumbered"]]})
                    self.report.add(f"update dcim/interfaces {label}: unnumbered")
            return
        await self._link_addresses(ends, list(ifaces), "p2p", self.fabric.p2p_prefix, label)

    async def _link_addresses(self, ends, ifaces, pool: str, pool_prefix, label: str) -> None:
        """Give both ends of a link an address from one /30 of the pool (NetBox picks it)."""
        ips = [await self.nb.find("ipam/ip-addresses", interface_id=i["id"]) for i in ifaces]
        if all(ips):
            return

        # Reuse the link's /30 if an earlier run allocated it; otherwise take the next free one.
        child = None
        for found in ips:
            if found:
                network = ipaddress.ip_interface(found[0]["address"]).network
                child = await self.nb.find_one("ipam/prefixes", prefix=str(network), vrf_id=self._ids["vrf"])
        if child is None:
            child = await self.nb.find_one(
                "ipam/prefixes", within=str(pool_prefix), description=label, vrf_id=self._ids["vrf"]
            )
        if child is None:
            child = await self.nb.allocate_prefix(
                self._ids[f"prefix:{pool}"],
                {
                    "prefix_length": 30,
                    "status": "active",
                    "scope_type": "dcim.site",
                    "scope_id": self._ids["site"],
                    "description": label,
                },
            )
            self.report.add(f"allocate ipam/prefixes {child['prefix']} for {label}")
        for (dev, port), iface, found in zip(ends, ifaces, ips):
            if not found:
                ip = await self.nb.allocate_ip(child["id"], _assign(iface))
                self.report.add(f"allocate ipam/ip-addresses {ip['address']} for {dev} {port}")

    # -- loopbacks -------------------------------------------------------------

    async def _loopbacks(self) -> None:
        for spine in self.fabric.spines:
            await self._loopback0(spine.name)
        if self.fabric.spine_pair is not None:   # gateway spines are VTEPs
            await self._vtep(self.fabric.spine_pair)
        for pair in self.fabric.leaf_pairs:
            for leaf in pair.leaves:
                await self._loopback0(leaf)
            await self._vtep(pair)

    async def _loopback0(self, device: str, name: str | None = None, pool: str = "loopback") -> None:
        """The device's own address on a loopback: LoopBack0, or a PIP's LoopBack1."""
        name = name or naming.loopback(self.fabric.vendor, 0)
        iface = await self._interface(device, name)
        found = await self.nb.find("ipam/ip-addresses", interface_id=iface["id"], role="loopback")
        if found:
            await self._host_mask(found[0], f"{device} {name}")
            return
        ip = await self.nb.allocate_ip(self._ids[f"prefix:{pool}"], {**_assign(iface), "role": "loopback"})
        ip = await self._host_mask(ip, None)
        self.report.add(f"allocate ipam/ip-addresses {ip['address']} for {device} {name}")

    async def _vtep(self, pair: LeafPair) -> None:
        pip = "prefix:pip" in self._ids   # the PIP layout: own PIP on LoopBack1, the VTEP on LoopBack2
        name = naming.loopback(self.fabric.vendor, 2 if pip else 1)
        if pip:
            for leaf in pair.leaves:
                await self._loopback0(leaf, naming.loopback(self.fabric.vendor, 1), "pip")
        ifaces = {leaf: await self._interface(leaf, name) for leaf in pair.leaves}
        found = {
            leaf: await self.nb.find("ipam/ip-addresses", interface_id=iface["id"], role="anycast")
            for leaf, iface in ifaces.items()
        }
        existing = {ipaddress.ip_interface(ip["address"]).ip for ips in found.values() for ip in ips}
        if len(existing) > 1:
            raise SeedError(f"leaves {list(pair.leaves)} have different VTEP IPs on {name}: {sorted(map(str, existing))}")

        first, second = pair.leaves
        if not found[first] and not found[second]:
            ip = await self.nb.allocate_ip(self._ids["prefix:vtep"], {**_assign(ifaces[first]), "role": "anycast"})
            ip = await self._host_mask(ip, None)
            self.report.add(f"allocate ipam/ip-addresses {ip['address']} for {first} {name} (VTEP)")
            found[first] = [ip]
        # The pair shares one VTEP address: the second leaf gets a copy of the allocated one.
        address = next(ip["address"] for ips in found.values() for ip in ips)
        for leaf in pair.leaves:
            if found[leaf]:
                await self._host_mask(found[leaf][0], f"{leaf} {name}")
            else:
                await self.nb.create(
                    "ipam/ip-addresses",
                    {**_assign(ifaces[leaf]), "address": _host(address), "role": "anycast", "vrf": self._ids["vrf"]},
                )
                self.report.add(f"create ipam/ip-addresses {_host(address)} for {leaf} {name} (VTEP)")

    async def _host_mask(self, ip: dict, label: str | None) -> dict:
        """Loopback addresses are /32; "next available" returns them with the pool's mask."""
        if ip["address"] == _host(ip["address"]):
            return ip
        updated = await self.nb.update("ipam/ip-addresses", ip["id"], {"address": _host(ip["address"])})
        if label:
            self.report.add(f"update ipam/ip-addresses {label}: address")
        return updated


def _global(prefix: dict) -> bool:
    return prefix.get("vrf") is None


def _assign(iface: dict) -> dict:
    return {"assigned_object_type": "dcim.interface", "assigned_object_id": iface["id"], "status": "active"}


# Set in the GUI after the first seed: NTP, SNMP location/contact and the gateway MAC.
GUI_KEYS = ("ntp_servers", "ntp_vpn_instance", "snmp", "gateway_mac", "netstream", "syslog",   # netstream:
            "microsegmentation", "qos", "port_profiles", "protection", "ospf_auth", "switch_login", "local_accounts")

# Prefix pools: (NetBox status, purpose). The pool's description is "<fabric name> <purpose>",
# which is how the GUI's switch additions find them again (orchestrator/fabric_build.py).
POOLS = {
    "loopback": ("active", "loopbacks"),
    "vtep": ("active", "VTEP anycast loopbacks"),
    "p2p": ("container", "spine-leaf point-to-point links"),
    "mlag_bypass": ("container", "M-LAG bypass links over the peer-link"),
    "gateways": ("container", "segment gateway subnets"),
    "pip": ("active", "VXLAN PIP loopbacks"),
}


def _host(address: str) -> str:
    return f"{ipaddress.ip_interface(address).ip}/32"


def _with_asn(context: dict, asn: int, loopback_prefix=None, pip_prefix=None) -> dict:
    """Generators read the fabric ASN from the config context: Annet's device model only
    carries the site's name, not its ASNs. Both copies come from the fabric YAML. The
    loopback pool tells BgpEvpn which BGP peers are fabric peers (other peers are
    routing objects it must not remove)."""
    fabric = {**context["fabric"], "asn": asn}
    if loopback_prefix is not None:
        fabric["loopback_prefix"] = str(loopback_prefix)
    if pip_prefix is not None:   # the PIP layout (generators/fabric.py pip_layout)
        fabric["pip_prefix"] = str(pip_prefix)
    return {"fabric": fabric}
