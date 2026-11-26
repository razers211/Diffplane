"""Add switches to an existing fabric from the GUI (the main way to add
new switches; a new fabric from the GUI comes later).

A spine (with its links to existing leaves) or an M-LAG leaf pair (peer-link and uplinks to
existing spines) is written to NetBox with the seed's own steps, so it gets exactly what a seeded
device gets: every front-panel port of its model, cables, a /30 per point-to-point link and the
M-LAG bypass, LoopBack0 and (pairs) the shared VTEP, all allocated by NetBox. New devices are
planned; they become active from the GUI (orchestrator/lifecycle.py). Nothing is deployed: the
existing switches the new links touch show the change as a diff.

Everything is checked before the first write. NetBox has no transactions: if a write fails
half-way, adding the same switch again completes it (each step looks up before it creates).
"""

import asyncio
import dataclasses
import ipaddress
import re
from types import SimpleNamespace

from orchestrator import documents, hostkeys, switch_inventory
from orchestrator.netbox import NetBoxClient
from orchestrator.segments import SegmentError
from seed.loader import POOLS, SeedError, Seeder
from seed.model import VENDOR, Fabric, FabricError, LeafPair, Link, MlagTrunk, interface_type
from seed import ports as seed_ports
from seed.ports import ports_of
from generators import naming

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}\Z")
LEAF_ROLES = ("leaf", "border-leaf")
MAX_TRUNK = 1023


def _role(device: dict) -> str | None:
    role = device.get("role")
    return role.get("slug") if isinstance(role, dict) else None


class FabricBuilder(Seeder):
    def __init__(self, netbox: NetBoxClient, fabric: str, new: Fabric | None = None, imported: bool = False):
        """`new`: a fabric made in the GUI, checked but not in NetBox yet; `add` creates its
        site, contexts and pools right before its first switches. `imported`: a fabric read from
        its switches: its planned placeholders may have no known uplink."""
        super().__init__(netbox, None)
        self.slug = fabric
        self.devices: dict[str, dict] = {}
        self.new = new
        self.imported = imported
        self.pools: set[str] = set()
        self.layouts: dict[str, dict[str, str]] = {}   # model -> ports, as detected switches of it reported

    def _fresh(self) -> None:
        f = self.new
        self.fabric = f
        self.context = None
        self.centralized = f.services.architecture == "centralized"
        self.gateway_pair = []
        self.pools = {key for key, prefix in (("loopback", f.loopback_prefix), ("vtep", f.vtep_prefix),
                                              ("p2p", f.p2p_prefix), ("mlag_bypass", f.mlag_bypass_prefix),
                                              ("gateways", f.gateways_prefix), ("pip", f.pip_prefix))
                      if prefix is not None}

    # -- the existing fabric ---------------------------------------------------------------

    async def _load(self) -> None:
        await seed_ports.refresh(self.nb)   # layouts learned from switches
        site, context, vrf = await asyncio.gather(
            self.nb.find_one("dcim/sites", slug=self.slug),
            documents.find(self.nb, f"fabric-{self.slug}"),
            self.nb.find_one("ipam/vrfs", name=f"fabric-{self.slug}"))
        if site is None or context is None or vrf is None:
            raise SegmentError(f"no seeded fabric {self.slug!r}")
        self._ids["site"], self._ids["vrf"] = site["id"], vrf["id"]
        pools = {}
        prefixes, devices = await asyncio.gather(self.nb.find("ipam/prefixes", vrf_id=vrf["id"]),
                                                 self.nb.find("dcim/devices", site_id=site["id"]))
        for key, (_, purpose) in POOLS.items():
            found = next((p for p in prefixes if p.get("description") == f"{site['name']} {purpose}"), None)
            if found is not None:
                self._ids[f"prefix:{key}"] = found["id"]
                pools[key] = ipaddress.ip_network(found["prefix"])
                self.pools.add(key)
        settings = (context.get("data") or {}).get("fabric") or {}
        vendor = settings.get("vendor") or VENDOR
        self.fabric = SimpleNamespace(
            name=site["name"], slug=self.slug, p2p_prefix=pools.get("p2p"), mlag_bypass_prefix=pools.get("mlag_bypass"),
            vendor=vendor, services=SimpleNamespace(mlag_bypass_vlan=(settings.get("mlag") or {}).get("bypass_vlan"),
                                                   vendor=vendor),
        )
        self.context = context
        self.centralized = settings.get("architecture") == "centralized"
        self.gateway_pair = sorted(settings.get("gateway_pair") or ())
        self.devices = {d["name"]: d for d in devices}
        for name, device in self.devices.items():
            self._ids[name] = device["id"]

    # -- checks ----------------------------------------------------------------------------

    async def _new_name(self, name: str) -> None:
        if not NAME.match(name or ""):
            raise SegmentError(f"{name!r}: a device name is 1-63 letters, digits, '.', '_' or '-'")
        if await self.nb.find("dcim/devices", name=name):
            raise SegmentError(f"device name {name!r} is already used (names are unique across fabrics)")

    def _model(self, model: str) -> list[str]:
        vendor = self.fabric.vendor
        ports = list(self.layouts.get(model) or ports_of(model))
        if not ports:
            raise SegmentError(f"model {model!r} has no known port layout (detect the switch from its management "
                               f"address so its ports are read, or add it by ZTP or import); "
                               f"known: {', '.join(seed_ports.known_models(vendor))}")
        if seed_ports.vendor_of_model(model) != vendor:
            raise SegmentError(f"{model} is not a {vendor} switch: a fabric is one vendor "
                               f"(known: {', '.join(seed_ports.known_models(vendor))})")
        return ports

    def _detected(self, name: str, model: str, address: str | None, detected: dict) -> dict | None:
        """The detected switch a new one is (by its management address): its model must be the one
        given, and every detected switch of a model must report the same ports."""
        hit = next((d for a, d in detected.items() if address and a.split("/")[0] == address.split("/")[0]), None)
        if hit is None:
            return None
        found = hit["found"]
        if found.model != model:
            raise SegmentError(f"{name} ({address}) reports model {found.model}, not {model}")
        if self.layouts.get(model, found.ports) != found.ports:
            raise SegmentError(f"two detected {model} switches report different ports: add them separately")
        self.layouts[model] = found.ports
        return hit

    async def _free_port(self, device: str, port: str) -> None:
        """A port of an existing device that a new cable may use."""
        iface = await self.nb.find_one("dcim/interfaces", device_id=self.devices[device]["id"], name=port)
        if iface is None:
            raise SegmentError(f"{device} has no port {port!r}")
        if (iface.get("type") or {}).get("value") in ("lag", "virtual") or naming.is_management(port):
            raise SegmentError(f"{device} {port} is not a front-panel port")
        if iface.get("cable") or iface.get("lag") or (iface.get("custom_fields") or {}).get("segments") \
                or await self.nb.find("ipam/ip-addresses", interface_id=iface["id"]):
            raise SegmentError(f"{device} {port} is in use (cabled, in a trunk, addressed or carrying segments)")

    def _existing(self, name: str, roles: tuple[str, ...], what: str) -> None:
        device = self.devices.get(name)
        if device is None or _role(device) not in roles:
            raise SegmentError(f"{name!r} is not a {what} of fabric {self.slug}")

    @staticmethod
    def _claim(used: set, device: str, port: str, ports: list[str] | None = None) -> None:
        if ports is not None and port not in ports:
            raise SegmentError(f"{device} has no port {port!r}")
        try:
            kind = interface_type(port)
        except FabricError as exc:
            raise SegmentError(str(exc)) from exc
        if kind in ("lag", "virtual") or naming.is_management(port):
            raise SegmentError(f"{device} {port} is not a front-panel port")
        if (device, port) in used:
            raise SegmentError(f"{device} {port} is used twice")
        used.add((device, port))

    async def options(self) -> dict:
        """What the GUI's add-switch form offers: known models with their ports, and the fabric's
        spines and leaves with their free front-panel ports (for new cables)."""
        await self._load()
        chosen = [(name, device) for name, device in sorted(self.devices.items()) if _role(device) in ("spine", *LEAF_ROLES)]
        found = await asyncio.gather(*(asyncio.gather(self.nb.find("dcim/interfaces", device_id=device["id"]),
                                                      self.nb.find("ipam/ip-addresses", device_id=device["id"]))
                                       for _, device in chosen))
        devices = []
        for (name, device), (interfaces, addresses) in zip(chosen, found):
            role = _role(device)
            routed = {ip.get("assigned_object_id") for ip in addresses}
            free = [i["name"] for i in interfaces
                    if (i.get("type") or {}).get("value") not in ("lag", "virtual") and not naming.is_management(i["name"])
                    and not i.get("cable") and not i.get("lag") and i["id"] not in routed
                    and not (i.get("custom_fields") or {}).get("segments")]
            devices.append({"name": name, "role": role, "free_ports": sorted(free, key=_port_key),
                            "paired": bool((device.get("custom_fields") or {}).get("mlag_peer")),
                            "model": ((device.get("device_type") or {}).get("model"))})
        return {"fabric": self.slug, "models": {m: ports_of(m) for m in seed_ports.known_models(self.fabric.vendor)},
                "devices": devices, "bypass": self.fabric.services.mlag_bypass_vlan is not None,
                "centralized": self.centralized, "gateway_pair": self.gateway_pair}

    # -- additions -------------------------------------------------------------------------

    async def add_spine(self, name: str, model: str, links: list[dict]) -> list[str]:
        """A spine and its links to existing leaves: [{spine_port, leaf, leaf_port}]."""
        result = await self.add({"spines": [{"name": name, "model": model}],
                                 "links": [{**link, "spine": name} for link in links]})
        return result["changes"]

    async def add_leaf_pair(self, leaves: list[str], model: str, role: str, peer_link_trunk: int,
                            peer_link_members: list[str], uplinks: list[dict]) -> list[str]:
        """Two leaves in an M-LAG pair, their peer-link and uplinks to existing spines."""
        for uplink in uplinks:
            _fields(uplink, ("leaf", "leaf_port", "spine", "spine_port"))
            # Only uplinks of the new pair here; unknown names are left to the batch's checks,
            # so a refused pair name is reported first.
            if uplink["leaf"] not in leaves and await self.nb.find("dcim/devices", name=uplink["leaf"]):
                raise SegmentError(f"uplink from {uplink['leaf']!r}: not one of the new leaves")
        result = await self.add({"pairs": [{"leaves": leaves, "model": model, "role": role,
                                            "peer_link_trunk": peer_link_trunk,
                                            "peer_link_members": peer_link_members}],
                                 "links": uplinks})
        return result["changes"]

    async def add(self, batch: dict, detected: dict | None = None) -> dict:
        """Several switches at once: {"spines": [{name, model, management?}],
        "pairs": [{leaves, model, role, peer_link_trunk, peer_link_members, management?: {leaf: address}}],
        "leaves": [{name, model, role, management?}] (lone leaves, no M-LAG peer),
        "spine_pairs": [{spines, model, peer_link_trunk, peer_link_members, management?, gateway?}]
        (centralized fabrics; one spine may be an existing single spine; gateway: becomes the
        fabric's gateway pair), "links": [{spine, spine_port, leaf, leaf_port}] (each with at least
        one new end)}.
        Everything is checked before the first write. A switch given a management address
        (with its mask) is activated right away; the others stay planned."""
        from orchestrator.lifecycle import Lifecycle

        if self.new is None:
            await self._load()
        else:
            self._fresh()
        await self._schema()   # tags and roles the seed's steps use (idempotent; about 20 requests)
        spines, pairs = batch.get("spines") or [], batch.get("pairs") or []
        lone, links = batch.get("leaves") or [], batch.get("links") or []
        spine_pairs = batch.get("spine_pairs") or []
        if not (spines or pairs or lone or spine_pairs):
            raise SegmentError("nothing to add")
        new: dict[str, tuple[str, str]] = {}   # name -> (model, role)
        management: dict[str, str] = {}
        stock: dict[str, dict] = {}            # name -> the inventory switch it is
        seen: dict[str, dict] = {}             # name -> its detected switch {"found", "pins"}

        async def name(n: str, model: str, role: str, address: str | None, serial: str | None = None) -> None:
            await self._new_name(n)
            if n in new:
                raise SegmentError(f"{n!r} appears twice in this addition")
            if hit := self._detected(n, model, address, detected or {}):
                if serial:
                    raise SegmentError(f"{n} is detected from its address: do not also pick it from the inventory")
                seen[n] = hit
            self._model(model)
            new[n] = (model, role)
            if address:
                management[n] = address
            if serial:
                stock[n] = await self._from_inventory(n, serial, model, stock)

        for spine in spines:
            await name(spine.get("name", ""), spine.get("model", ""), "spine", spine.get("management"),
                       spine.get("inventory"))
        used: set = set()
        built_pairs = []
        gateway_choices: list[list[str]] = []
        for pair in pairs:
            leaves = list(pair.get("leaves") or [])
            if len(leaves) != 2 or leaves[0] == leaves[1]:
                raise SegmentError("a leaf pair is two different names")
            role, model = pair.get("role", "leaf"), pair.get("model", "")
            if role not in LEAF_ROLES:
                raise SegmentError(f"a leaf pair's role is {' or '.join(LEAF_ROLES)}")
            for leaf in leaves:
                await name(leaf, model, role, (pair.get("management") or {}).get(leaf),
                           (pair.get("inventory") or {}).get(leaf))
            trunk, members = self._peer_link(pair)
            for leaf in leaves:
                for member in members:
                    self._claim(used, leaf, member, self._model(model))
            mlag = self._mlag_trunks(pair, trunk)
            for leaf in leaves:
                for t in mlag:
                    for member in t.members:
                        self._claim(used, leaf, member, self._model(model))
            built_pairs.append(LeafPair(leaves=(leaves[0], leaves[1]), model=model, role=role,
                                        peer_link_trunk=trunk, peer_link_members=tuple(members), mlag_trunks=mlag,
                                        vendor=self.fabric.vendor))
            if pair.get("gateway"):   # a border-leaf pair as a centralized fabric's gateway pair
                if not self.centralized:
                    raise SegmentError(f"{self.slug} is symmetric: it has no gateway pair")
                if role != "border-leaf":
                    raise SegmentError("a gateway pair of leaves has role border-leaf")
                gateway_choices.append(sorted(leaves))
        # M-LAG spine pairs: in a centralized fabric spines also act as border
        # leaves. Two new spines, or a new one with an existing single spine; the pair may become
        # the fabric's gateway pair.
        for pair in spine_pairs:
            names = list(pair.get("spines") or [])
            if len(names) != 2 or names[0] == names[1]:
                raise SegmentError("a spine pair is two different names")
            if not self.centralized:
                raise SegmentError(f"{self.slug} is symmetric: M-LAG spine pairs are for a centralized fabric")
            model = pair.get("model", "")
            if all(n in self.devices for n in names):
                raise SegmentError(f"{' and '.join(names)} both exist: a spine pair needs at least one new spine")
            trunk, members = self._peer_link(pair)
            for n in names:
                if n in self.devices:   # an existing spine joins the pair
                    self._existing(n, ("spine",), "spine")
                    if (self.devices[n].get("custom_fields") or {}).get("mlag_peer"):
                        raise SegmentError(f"{n} is already in an M-LAG pair")
                    if await self.nb.find("dcim/interfaces", device_id=self.devices[n]["id"], name=naming.trunk(self.fabric.vendor, trunk)):
                        raise SegmentError(f"{n} already has {naming.trunk(self.fabric.vendor, trunk)}: pick another peer-link trunk number")
                    for member in members:
                        self._claim(used, n, member)
                        await self._free_port(n, member)
                else:
                    await name(n, model, "spine", (pair.get("management") or {}).get(n),
                               (pair.get("inventory") or {}).get(n))
                    for member in members:
                        self._claim(used, n, member, self._model(model))
            if pair.get("gateway"):
                gateway_choices.append(sorted(names))
            mlag = self._mlag_trunks(pair, trunk)
            if mlag and any(n in self.devices for n in names):
                raise SegmentError("M-LAG trunks come with two new spines only; add them in Trunks afterwards")
            for n in names:
                for t in mlag:
                    for member in t.members:
                        self._claim(used, n, member, self._model(model))
            built_pairs.append(LeafPair(leaves=(names[0], names[1]), model=model, role="spine",
                                        peer_link_trunk=trunk, peer_link_members=tuple(members), mlag_trunks=mlag,
                                        vendor=self.fabric.vendor))
        if len(gateway_choices) > 1:
            raise SegmentError("only one pair can become the gateway pair")
        new_gateways = gateway_choices[0] if gateway_choices else None
        if self.new is not None and self.centralized and new_gateways is None:
            raise SegmentError("a centralized fabric needs its gateway pair: mark one spine pair or "
                               "border-leaf pair as the gateway pair")
        if new_gateways is not None and self.gateway_pair:
            await self._gateway_move(new_gateways)
        for leaf in lone:
            role = leaf.get("role", "leaf")
            if role not in LEAF_ROLES:
                raise SegmentError(f"a leaf's role is {' or '.join(LEAF_ROLES)}")
            await name(leaf.get("name", ""), leaf.get("model", ""), role, leaf.get("management"), leaf.get("inventory"))
        if (pairs or lone or spine_pairs) and "vtep" not in self.pools:
            raise SegmentError(f"{self.slug} has no VTEP pool")

        wanted, trunk_uplinks = [], set()
        for link in links:
            _fields(link, ("spine", "spine_port", "leaf", "leaf_port"))
            spine, leaf = link["spine"], link["leaf"]
            if self.imported and link.get("trunk"):
                trunk_uplinks.add(leaf)   # over Eth-Trunks: the import writes it with its trunks
                continue
            if spine not in new and leaf not in new:
                raise SegmentError(f"{spine} - {leaf}: a link needs at least one new switch")
            for device, port, roles, what in ((spine, link["spine_port"], ("spine",), "spine"),
                                              (leaf, link["leaf_port"], LEAF_ROLES, "leaf")):
                if device in new:
                    if new[device][1] not in roles:
                        raise SegmentError(f"{device!r} is not a {what}")
                    self._claim(used, device, port, self._model(new[device][0]))
                else:
                    self._existing(device, roles, what)
                    self._claim(used, device, port)
                    await self._free_port(device, port)
            wanted.append(Link(spine=spine, spine_port=link["spine_port"], leaf=leaf, leaf_port=link["leaf_port"],
                               unnumbered=self.imported and bool(link.get("unnumbered"))))
        if any(not link.unnumbered for link in wanted) and self.fabric.p2p_prefix is None:
            raise SegmentError(f"{self.slug} has no point-to-point pool")
        has_spines = any(_role(d) == "spine" for d in self.devices.values()) or bool(spines)
        for n, (_, role) in new.items():
            if self.imported and n not in management:   # a placeholder: its own uplinks were not read
                continue
            if role != "spine" and has_spines and not any(link.leaf == n for link in wanted) and n not in trunk_uplinks:
                raise SegmentError(f"{n} needs at least one uplink to a spine")

        addresses = set()
        for n, address in management.items():
            try:
                iface = ipaddress.IPv4Interface(address)
            except ValueError as exc:
                raise SegmentError(f"{n}: invalid management address: {exc}") from exc
            if "/" not in address or iface.network.prefixlen >= 31:
                raise SegmentError(f"{n}: give the management address with its mask, e.g. 172.24.0.120/24")
            if iface.ip in addresses:
                raise SegmentError(f"{iface.ip} is given twice")
            addresses.add(iface.ip)
            if [ip for ip in await self.nb.find("ipam/ip-addresses", address=str(iface.ip)) if ip.get("vrf") is None]:
                raise SegmentError(f"{iface.ip} is already a management address in NetBox")

        async def build() -> None:
            if self.new is not None:   # the fabric itself first: site, ASN, contexts, VRF, pools
                if new_gateways is not None:
                    self.fabric = dataclasses.replace(self.new, services=dataclasses.replace(
                        self.new.services, gateway_pair=tuple(new_gateways)))
                await self._site()
            await self._learn_layouts()
            for model in sorted({m for m, _ in new.values()}):
                await self._device_type(model)
            for n, (model, role) in new.items():
                await self._device(n, model, role)
            for pair in built_pairs:
                await self._pair(pair)
            for link in wanted:
                await self._spine_link(link)
            if self.new is not None:   # the seed's order, so addresses are those of the same YAML
                for n, (_, role) in new.items():
                    if role == "spine":
                        await self._loopback0(n)
                for pair in built_pairs:
                    if pair.role == "spine":
                        await self._vtep(pair)
                for pair in built_pairs:
                    if pair.role != "spine":
                        for leaf in pair.leaves:
                            await self._loopback0(leaf)
                        await self._vtep(pair)
                for leaf in lone:
                    await self._loopback0(leaf["name"])
                    await self._own_vtep(leaf["name"])
                return
            for n in new:
                await self._loopback0(n)
            for pair in built_pairs:
                await self._vtep(pair)
            for leaf in lone:
                await self._own_vtep(leaf["name"])

        await self._write(build)
        await self._take_from_inventory(stock)
        if new_gateways is not None and self.new is None:
            data = dict(self.context.get("data") or {})
            data["fabric"] = {**(data.get("fabric") or {}), "gateway_pair": new_gateways}
            await documents.save(self.nb, self.context, data)
            self.report.add(f"update extras/config-contexts fabric-{self.slug}: gateway_pair {'+'.join(new_gateways)}")
        for n, address in management.items():
            await Lifecycle(self.nb).activate(n, address)
        for n, hit in seen.items():   # the keys an approver approved, then what the switch reported
            await self.nb.update("dcim/devices", self._ids[n], {"custom_fields": {
                hostkeys.FIELD: {str(port): keys for port, keys in hit["pins"].items()}}})
            self.report.add(f"pin {n}'s host keys (approved when it was detected)")
            await switch_inventory.learn(self.nb, n, hit["found"])
        return {"devices": list(new), "active": sorted(management), "changes": self.report.changes,
                "gateway_pair": new_gateways}

    async def _learn_layouts(self) -> None:
        """Detected models' ports as their device types' interface templates (as switch_inventory
        does), so the seed's steps create exactly those ports."""
        for model, layout in sorted(self.layouts.items()):
            await switch_inventory.templates(self.nb, await switch_inventory.device_type(self.nb, model), layout)
            self.report.add(f"learn the ports of {model} ({len(layout)}) from the detected switch")
        if self.layouts:
            await seed_ports.refresh(self.nb)

    async def _from_inventory(self, name: str, serial: str, model: str, stock: dict[str, dict]) -> dict:
        """The inventory switch a new switch is: same model, picked once."""
        from orchestrator.inventory import Inventory

        device = await Inventory(self.nb).get(serial)
        if (device.get("device_type") or {}).get("model") != model:
            raise SegmentError(f"inventory switch {serial} is a {(device.get('device_type') or {}).get('model')}, "
                               f"not a {model} as {name}")
        if any(d["id"] == device["id"] for d in stock.values()):
            raise SegmentError(f"inventory switch {serial} is picked twice")
        return device

    async def _take_from_inventory(self, stock: dict[str, dict]) -> None:
        """Each new switch picked from the inventory gets its serial and MEth MAC; the inventory
        entry goes."""
        from orchestrator.inventory import MAC_FIELD, mac_field

        if stock:
            await mac_field(self)
        for n, device in stock.items():
            await self.nb.update("dcim/devices", self._ids[n], {
                "serial": device.get("serial") or "",
                "custom_fields": {MAC_FIELD: (device.get("custom_fields") or {}).get(MAC_FIELD)}})
            await self.nb.delete("dcim/devices", device["id"])
            self.report.add(f"take {n} from the inventory (serial {device.get('serial')})")

    def _mlag_trunks(self, pair: dict, peer_link: int) -> tuple[MlagTrunk, ...]:
        """A new pair's M-LAG server trunks (an imported pair, the GUI adds them afterwards)."""
        trunks = []
        for t in pair.get("mlag_trunks") or []:
            number, members = t.get("trunk"), tuple(t.get("members") or ())
            if not isinstance(number, int) or not 1 <= number <= MAX_TRUNK or number == peer_link:
                raise SegmentError(f"M-LAG trunk number {number!r}: 1-{MAX_TRUNK}, not the peer-link's")
            if not members:
                raise SegmentError(f"{naming.trunk(self.fabric.vendor, number)} needs at least one member")
            if number in (x.trunk for x in trunks):
                raise SegmentError(f"{naming.trunk(self.fabric.vendor, number)} is given twice")
            trunks.append(MlagTrunk(trunk=number, members=members, vendor=self.fabric.vendor))
        return tuple(trunks)

    def _peer_link(self, pair: dict) -> tuple[int, list[str]]:
        trunk, members = pair.get("peer_link_trunk", 0), list(pair.get("peer_link_members") or [])
        if not isinstance(trunk, int) or not 0 <= trunk <= MAX_TRUNK:
            raise SegmentError(f"the peer-link trunk number must be 0-{MAX_TRUNK}")
        if not members:
            raise SegmentError("the peer-link needs at least one member port")
        if self.fabric.services.mlag_bypass_vlan is None or self.fabric.mlag_bypass_prefix is None:
            raise SegmentError(f"{self.slug} has no M-LAG bypass VLAN or pool, which an M-LAG pair needs")
        return trunk, members

    async def _gateway_move(self, new: list[str]) -> None:
        """Refuse moving the gateways when something stays behind on the old pair: a service
        (its routes need the segments' VPN instances there), or routing intent using a segment
        VPN instance that would leave a device."""
        from orchestrator.routing import Routing
        from orchestrator.routing_intent import RoutingStore, vpns_on

        old = set(self.gateway_pair)
        services = await Routing(self.nb).services(self.slug)
        for service in services:
            if set(service.get("devices") or []) & old:
                raise SegmentError(f"service {service['name']} is on the gateway pair {' and '.join(sorted(old))}; "
                                   "delete it before moving the gateways")
        store = RoutingStore(self.nb)
        _, segments = await store.fabric_state(self.slug)
        for target, routing in (await store.load(self.slug)).devices.items():
            uses = routing.vpn_uses()
            for device in target.split("+"):
                gone = vpns_on(device, services, segments, old) - vpns_on(device, services, segments, set(new))
                for vpn in sorted(gone & set(uses)):
                    raise SegmentError(f"moving the gateways would remove VPN instance {vpn} from {device}, which "
                                       f"the routing intent of {target} uses ({', '.join(uses[vpn])}); change it first")

    async def _own_vtep(self, leaf: str) -> None:
        """A lone leaf's own VTEP address on LoopBack1, from the VTEP pool."""
        iface = await self._interface(leaf, naming.loopback(self.fabric.vendor, 1))
        if await self.nb.find("ipam/ip-addresses", interface_id=iface["id"], role="anycast"):
            return
        ip = await self.nb.allocate_ip(self._ids["prefix:vtep"],
                                       {"assigned_object_type": "dcim.interface", "assigned_object_id": iface["id"],
                                        "status": "active", "role": "anycast"})
        await self._host_mask(ip, None)
        self.report.add(f"allocate ipam/ip-addresses {ip['address']} for {leaf} {naming.loopback(self.fabric.vendor, 1)} (VTEP)")

    # -- removal ---------------------------------------------------------------------------

    async def removal(self, name: str) -> dict:
        """What removing a planned switch takes with it, or why it is refused. A leaf of an M-LAG
        pair goes with its peer: a pair is added and removed whole. A spine of a spine pair goes
        alone and its peer (planned too) becomes a single spine again: a pair may have been made
        with an existing spine."""
        from orchestrator.routing import Routing
        from orchestrator.routing_intent import RoutingStore

        await self._load()
        device = self.devices.get(name)
        if device is None:
            raise SegmentError(f"{name!r} is not a device of fabric {self.slug}")
        peer = (device.get("custom_fields") or {}).get("mlag_peer")
        peer_id = peer.get("id") if isinstance(peer, dict) else peer
        peers = [d for d in self.devices.values() if peer_id is not None and d["id"] == peer_id]
        unpair = peers[0] if peers and _role(device) == "spine" else None
        targets = [device] if unpair else [device] + peers
        names = sorted(d["name"] for d in targets)
        gateways = set(self.gateway_pair)   # first: the most useful reason
        if gateways & {*names, *([unpair["name"]] if unpair else [])}:
            raise SegmentError(f"{' and '.join(sorted(gateways))} are the fabric's gateway pair")
        for d in targets + ([unpair] if unpair else []):
            if d in targets and (d.get("custom_fields") or {}).get("ztp"):
                raise SegmentError(f"{d['name']} is registered for zero-touch provisioning; unregister it first")
            status = d.get("status")
            if (status.get("value") if isinstance(status, dict) else status) != "planned":
                raise SegmentError(f"{d['name']} is active; decommission it first")
        for service in await Routing(self.nb).services(self.slug):
            if set(service.get("devices") or []) & set(names):
                raise SegmentError(f"service {service['name']} is on {' and '.join(names)}; delete it first")
        document = await RoutingStore(self.nb).load(self.slug)
        for key in document.devices:
            if set(key.split("+")) & set(names):
                raise SegmentError(f"routing intent of {key} still exists; empty it first")
        cables, ips, links = set(), [], []
        target_ids = {d["id"] for d in targets}
        for d in targets:
            for iface in await self.nb.find("dcim/interfaces", device_id=d["id"]):
                if (iface.get("custom_fields") or {}).get("segments"):
                    raise SegmentError(f"{d['name']} {iface['name']} carries segments; detach them first")
                tags = {t.get("slug") for t in iface.get("tags") or [] if isinstance(t, dict)}
                if (iface.get("type") or {}).get("value") == "lag" and "peer-link" not in tags:
                    raise SegmentError(f"{d['name']} has {iface['name']}; delete its trunks first")
                ips += await self.nb.find("ipam/ip-addresses", interface_id=iface["id"])
                cable = iface.get("cable")
                if cable:
                    cables.add(cable.get("id") if isinstance(cable, dict) else cable)
                    for far in iface.get("link_peers") or []:
                        far_device = (far.get("device") or {})
                        if far_device.get("id") not in target_ids:
                            links.append(f"{far_device.get('name')} {far['name']}")
        return {"devices": names, "cables": sorted(cables), "ips": ips, "links": sorted(links),
                "unpair": unpair["name"] if unpair else None}

    async def remove(self, name: str) -> dict:
        """Remove a planned switch (with its M-LAG peer) from NetBox: its cables, its fabric
        addresses with their /30s (the far end of each link too), then the device and its ports.
        Its config, if any was ever deployed, is not touched (it was decommissioned first)."""
        plan = await self.removal(name)
        networks = set()
        for ip in plan["ips"]:
            iface = ipaddress.ip_interface(ip["address"])
            if iface.network.prefixlen in (30, 31):
                networks.add(str(iface.network))
        for cable in plan["cables"]:
            await self.nb.delete("dcim/cables", cable)
        vrf = self._ids["vrf"]
        for network in sorted(networks):
            # Both ends of a point-to-point or bypass link, and the /30 (/31) itself.
            for ip in await self.nb.find("ipam/ip-addresses", parent=network, vrf_id=vrf):
                await self.nb.delete("ipam/ip-addresses", ip["id"])
            for prefix in await self.nb.find("ipam/prefixes", prefix=network, vrf_id=vrf):
                await self.nb.delete("ipam/prefixes", prefix["id"])
        for ip in plan["ips"]:   # loopbacks, VTEP: not left behind unassigned
            if await self.nb.find("ipam/ip-addresses", id=ip["id"]):
                await self.nb.delete("ipam/ip-addresses", ip["id"])
        if plan["unpair"]:
            await self._unpair(plan["unpair"])
        for device in plan["devices"]:
            found = await self.nb.find_one("dcim/devices", name=device)
            if found is not None:
                await self.nb.delete("dcim/devices", found["id"])   # its ports and their addresses go with it
        return {"devices": plan["devices"], "links": plan["links"], "unpaired": plan["unpair"]}

    async def _unpair(self, name: str) -> None:
        """The staying spine of a removed spine pair: no peer, peer-link, bypass Vlanif or VTEP.
        Its cables and the bypass /30 went with the removed spine."""
        device = await self.nb.find_one("dcim/devices", name=name)
        interfaces = await self.nb.find("dcim/interfaces", device_id=device["id"])
        bypass = naming.svi(self.fabric.vendor, self.fabric.services.mlag_bypass_vlan)
        for iface in interfaces:
            tags = {t.get("slug") for t in iface.get("tags") or [] if isinstance(t, dict)}
            if "peer-link" in tags:
                for member in interfaces:
                    lag = member.get("lag")
                    if (lag.get("id") if isinstance(lag, dict) else lag) == iface["id"]:
                        await self.nb.update("dcim/interfaces", member["id"], {"lag": None})
                await self.nb.delete("dcim/interfaces", iface["id"])
            elif iface["name"] in (naming.loopback(self.fabric.vendor, 1), naming.loopback(self.fabric.vendor, 2), bypass):
                for ip in await self.nb.find("ipam/ip-addresses", interface_id=iface["id"]):
                    await self.nb.delete("ipam/ip-addresses", ip["id"])
                await self.nb.delete("dcim/interfaces", iface["id"])
        await self.nb.update("dcim/devices", device["id"], {"custom_fields": {"mlag_peer": None}})

    @staticmethod
    async def _write(steps) -> None:
        try:
            await steps()
        except SeedError as exc:
            raise SegmentError(str(exc)) from exc


def _port_key(name: str) -> tuple:
    return tuple(int(n) if n.isdigit() else n for n in re.split(r"(\d+)", name))


def _fields(link: dict, keys: tuple[str, ...]) -> None:
    missing = [k for k in keys if not link.get(k)]
    if missing:
        raise SegmentError(f"a link needs {', '.join(keys)} (missing {', '.join(missing)})")
