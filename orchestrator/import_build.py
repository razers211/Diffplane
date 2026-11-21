"""Write an imported fabric with the builder's new-fabric steps, placing the
addresses the switches already use instead of allocating new ones.

The seed's steps allocate every fabric address from the pools ("next available", never
computed by us). For an imported fabric the addresses exist already: `GivenAddresses` answers
those allocations with the switch's own address (an existing address is recorded, not chosen),
and lets NetBox allocate only what no switch told (a placeholder whose address is unknown).
The same code builds the read-only preview in an in-memory NetBox (orchestrator/
import_preview.py) and, later, the real fabric.
"""

import ipaddress
import re

from orchestrator import documents
from orchestrator import fabric_new
from orchestrator.fabric_import import ImportError_, ImportPlan
from orchestrator.overrides import check as check_overrides
from orchestrator.segments import SegmentError

OVERRIDES = "overrides"


def link_label(spine: str, spine_port: str, leaf: str, leaf_port: str) -> str:
    """The description the seed gives a point-to-point /30 (seed/loader.py _spine_link)."""
    return f"{spine} {spine_port} - {leaf} {leaf_port}"


def bypass_label(a: str, b: str) -> str:
    return f"{a}-{b} M-LAG bypass"


class GivenAddresses:
    """A NetBox client whose allocations return the addresses given for each interface/link."""

    def __init__(self, netbox, addresses: dict[tuple[str, str], str], prefixes: dict[str, str]):
        self.nb = netbox
        self.addresses = addresses
        self.prefixes = prefixes

    def __getattr__(self, name):
        return getattr(self.nb, name)

    async def _parent(self, prefix_id: int, value: str, what: str) -> dict:
        parent = await self.nb.find_one("ipam/prefixes", id=prefix_id)
        network = ipaddress.ip_network(parent["prefix"])
        if not ipaddress.ip_network(value, strict=False).subnet_of(network):
            raise SegmentError(f"{what} {value} is outside the pool {network}: widen the pool")
        return parent

    @staticmethod
    def _vrf(parent: dict):
        vrf = parent.get("vrf")
        return vrf.get("id") if isinstance(vrf, dict) else vrf

    async def allocate_ip(self, prefix_id: int, data: dict) -> dict:
        iface = await self.nb.find_one("dcim/interfaces", id=data["assigned_object_id"])
        key = ((iface.get("device") or {}).get("name"), iface["name"])
        given = self.addresses.get(key)
        if given is None:
            return await self.nb.allocate_ip(prefix_id, data)
        parent = await self._parent(prefix_id, given, f"{key[0]} {key[1]} address")
        return await self.nb.create("ipam/ip-addresses", {**data, "address": given, "vrf": self._vrf(parent)})

    async def allocate_prefix(self, prefix_id: int, data: dict) -> dict:
        given = self.prefixes.get(data.get("description"))
        if given is None:
            return await self.nb.allocate_prefix(prefix_id, data)
        parent = await self._parent(prefix_id, given, data.get("description"))
        body = {k: v for k, v in data.items() if k != "prefix_length"}
        return await self.nb.create("ipam/prefixes", {**body, "prefix": given, "vrf": self._vrf(parent)})


def builder_input(plan: ImportPlan, name: str, pools: dict[str, str] | None = None,
                  gateway_mac: str | None = None) -> tuple[dict, dict, dict, dict]:
    """(settings, switches batch, addresses, prefixes) for fabric_new.create, from the plan and the
    operator's choices (fabric name, pools, gateway MAC)."""
    pools = {**plan.pools, **{k: v for k, v in (pools or {}).items() if v}}
    settings = {
        "name": name, "asn": plan.asn, "architecture": plan.architecture,
        "prefixes": pools, "bypass_vlan": plan.bypass_vlan, "gateway_mac": gateway_mac or plan.gateway_mac,
        "ntp_servers": list(plan.ntp_servers), "ntp_vpn_instance": plan.ntp_vpn,
        "snmp_location": plan.snmp_location, "snmp_contact": plan.snmp_contact,
    }
    if plan.asn is None:
        raise ImportError_("no switch runs BGP: the fabric ASN is unknown")
    paired = {d for p in plan.pairs for d in p.devices}
    batch: dict = {"spines": [], "pairs": [], "leaves": [], "spine_pairs": [], "links": []}
    for device in sorted(plan.devices.values(), key=lambda d: d.name):
        if device.name in paired:
            continue
        entry = {"name": device.name, "model": device.model, "management": device.management}
        if device.role == "spine":
            batch["spines"].append(entry)
        else:
            batch["leaves"].append({**entry, "role": device.role or "leaf"})
    for pair in plan.pairs:
        a, b = (plan.devices[d] for d in pair.devices)
        if a.model != b.model:
            raise ImportError_(f"{a.name} and {b.name} are different models ({a.model}, {b.model})")
        entry = {"model": a.model, "peer_link_trunk": pair.peer_link_trunk,
                 "peer_link_members": list(pair.peer_link_members),
                 "management": {d.name: d.management for d in (a, b) if d.management},
                 "mlag_trunks": [{"trunk": n, "members": list(m)} for n, m in sorted(pair.mlag_trunks.items())]}
        if a.role == "spine":
            batch["spine_pairs"].append({**entry, "spines": list(pair.devices),
                                         "gateway": plan.gateway_pair == pair.devices})
        else:
            batch["pairs"].append({**entry, "leaves": list(pair.devices), "role": a.role or "leaf",
                                   "gateway": plan.gateway_pair == pair.devices})
    addresses: dict[tuple[str, str], str] = {}
    prefixes: dict[str, str] = {}
    for link in plan.links:
        batch["links"].append({"spine": link.spine, "spine_port": link.spine_port,
                               "leaf": link.leaf, "leaf_port": link.leaf_port, "unnumbered": link.unnumbered,
                               "trunk": bool(link.members)})   # an Eth-Trunk link: written with the trunks
        if link.unnumbered:
            continue
        prefixes[link_label(link.spine, link.spine_port, link.leaf, link.leaf_port)] = str(link.network)
        addresses[(link.spine, link.spine_port)] = str(link.spine_address)
        addresses[(link.leaf, link.leaf_port)] = str(link.leaf_address)
    for pair in plan.pairs:
        if len(pair.bypass) == 2:
            a, b = pair.devices
            prefixes[bypass_label(a, b)] = str(pair.bypass[a].network)
            for device, address in pair.bypass.items():
                addresses[(device, f"Vlanif{plan.bypass_vlan}")] = str(address)
    for device in plan.devices.values():
        if device.loopback is not None:
            addresses[(device.name, "LoopBack0")] = f"{device.loopback.ip}/32"
        if device.pip is not None:
            addresses[(device.name, "LoopBack1")] = f"{device.pip.ip}/32"
        if device.vtep is not None:
            addresses[(device.name, "LoopBack2" if device.pip else "LoopBack1")] = f"{device.vtep.ip}/32"
    return settings, batch, addresses, prefixes


def slug_of(name: str) -> str:
    """The site slug of a fabric name (seed/model.py Fabric.slug)."""
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")


async def build(netbox, plan: ImportPlan, name: str, pools: dict[str, str] | None = None,
                gateway_mac: str | None = None, configs: dict[str, str] | None = None) -> dict:
    """Write the imported fabric: site, settings, pools, switches with their addresses (the
    read ones active with their management address), the adopted values, then its tenants with
    the orchestrator's own steps (single trunks, segments, services, routing, attachments) and
    the routing objects of each switch's running config (`configs`) as routing intent."""
    settings, batch, addresses, prefixes = builder_input(plan, name, pools, gateway_mac)
    check_overrides(plan.overrides)
    nb = GivenAddresses(netbox, addresses, prefixes)
    result = await fabric_new.create(nb, settings, batch, imported=True)
    slug = result["site"]
    site = await netbox.find_one("dcim/sites", slug=slug)
    if plan.overrides:
        await documents.create(netbox, f"{OVERRIDES}-{slug}",
                               {OVERRIDES: plan.overrides, "misfits": plan.misfits}, [site["id"]])
    await tenants(nb, plan, slug)
    if configs:
        await routing_intent(netbox, plan, slug, configs)
    return result


async def tenants(nb: GivenAddresses, plan: ImportPlan, slug: str) -> None:
    from orchestrator.routing import Routing
    from orchestrator.segments import Segments
    from orchestrator.trunks import Trunks

    for device, number, members in plan.trunks:
        await Trunks(nb).create(device, number, list(members), mlag=False)
    await trunk_links(nb, plan)
    await peer_link_cables(nb, plan)
    segments, routing = Segments(nb), Routing(nb)
    ordered = sorted(plan.segments.values(), key=lambda s: s.vni)
    for segment in ordered:
        await segments.create(slug, segment.name, segment.vni, segment.bd)
    for service in sorted(plan.services.values(), key=lambda s: s.name):
        if service.next_hop is None:
            continue   # a misfit already
        if service.port is None or service.asn == plan.asn:
            await imported_service(nb, slug, service)
            continue
        await routing.create_service(slug, service.name, service.devices[0], service.port, service.vlan,
                                     str(service.address), service.next_hop, service.asn, service.kind,
                                     dict(service.addresses) or None)
    routed = [s for s in ordered if s.gateway is not None]
    for segment in routed:   # the gateway subnet the switches use, not the next free one
        nb.prefixes[f"{slug}:{segment.name} gateway"] = str(segment.gateway.network)
        await routing.set_routing(slug, segment.vni, True, segment.gateway.network.prefixlen,
                                  firewall=segment.firewall, peers=[])
    for segment in routed:
        if segment.peers:
            await routing.set_routing(slug, segment.vni, True, segment.gateway.network.prefixlen,
                                      firewall=segment.firewall, peers=sorted(segment.peers))
    done = set()
    for device, port, vni, vid in plan.attachments:
        pair = next((p.devices for p in plan.pairs if device in p.devices), (device,))
        key = (pair if port.startswith("Eth-Trunk") else (device,), port, vni)
        if key in done:
            continue   # an M-LAG trunk's attachment is the pair's: written on both leaves at once
        done.add(key)
        await segments.attach(slug, device, port, vni, vid)


async def imported_service(nb, slug: str, service) -> None:
    """A service with no port of its own (its VLAN on VLAN trunks) or with iBGP to its firewall
    (an imported fabric's firewall contexts): written by the import alone, as create_service
    would store it, since the API makes only services on a dedicated port with eBGP."""
    from orchestrator.segments import Segments

    site = await nb.find_one("dcim/sites", slug=slug)
    device = await nb.find_one("dcim/devices", name=service.devices[0])
    await nb.create("ipam/vrfs", {
        "name": f"{slug}:{service.name}", "enforce_unique": False,
        "description": f"{slug} {service.kind} service {service.name} (device VPN instance)",
        "custom_fields": {"fabric": site["id"], "service_device": device["id"], "service_port": service.port,
                          "service_vlan": service.vlan, "service_address": str(service.address),
                          "service_next_hop": service.next_hop, "service_asn": service.asn,
                          "service_kind": service.kind, "service_addresses": dict(service.addresses) or None,
                          "service_ipv6": None}})
    await Segments(nb).publish(slug)


async def trunk_links(nb, plan: ImportPlan) -> None:
    """Unnumbered fabric links over Eth-Trunks (imported fabrics): both trunks tagged `unnumbered`
    and their member ports cabled as the switches' LLDP saw them."""
    from orchestrator.segments import UNNUMBERED_TAG

    tag = await nb.find_one("extras/tags", slug=UNNUMBERED_TAG)

    async def iface(device: str, name: str) -> dict:
        found = await nb.find_one("dcim/devices", name=device)
        return await nb.find_one("dcim/interfaces", device_id=found["id"], name=name)

    for link in (l for l in plan.links if l.members):
        for device, trunk in ((link.spine, link.spine_port), (link.leaf, link.leaf_port)):
            lag = await iface(device, trunk)
            ids = [t["id"] for t in lag.get("tags") or []]
            await nb.update("dcim/interfaces", lag["id"], {"tags": [*ids, tag["id"]]})
        for spine_port, leaf_port in link.members:
            a, b = await iface(link.spine, spine_port), await iface(link.leaf, leaf_port)
            await nb.create("dcim/cables", {
                "a_terminations": [{"object_type": "dcim.interface", "object_id": a["id"]}],
                "b_terminations": [{"object_type": "dcim.interface", "object_id": b["id"]}], "status": "connected"})


async def peer_link_cables(nb, plan: ImportPlan) -> None:
    """A peer-link whose members LLDP sees crossed: the builder cabled each member to its
    namesake, so those cables are replaced by the ones the switches see."""
    from orchestrator.segments import _ref_id

    for pair in (p for p in plan.pairs if p.cabling):
        a = await nb.find_one("dcim/devices", name=pair.devices[0])
        b = await nb.find_one("dcim/devices", name=pair.devices[1])
        ports = {}
        for device in (a, b):
            for port in pair.peer_link_members:
                ports[device["name"], port] = iface = await nb.find_one("dcim/interfaces", device_id=device["id"], name=port)
                if iface.get("cable"):
                    await nb.delete("dcim/cables", _ref_id(iface["cable"]))
        for port_a, port_b in pair.cabling:
            await nb.create("dcim/cables", {
                "a_terminations": [{"object_type": "dcim.interface", "object_id": ports[a["name"], port_a]["id"]}],
                "b_terminations": [{"object_type": "dcim.interface", "object_id": ports[b["name"], port_b]["id"]}],
                "status": "connected"})


async def routing_intent(netbox, plan: ImportPlan, slug: str, configs: dict[str, str]) -> None:
    """Hand-made routing objects become intent (as the routing auto-import adopts them): per M-LAG
    pair what both read leaves have alike, else per device. What cannot be expressed is a misfit."""
    from orchestrator.routing_import import RoutingImporter, common, drop_dangling, routing_objects
    from orchestrator.routing_intent import DeviceRouting

    importer = RoutingImporter(netbox, annet=None)
    targets: dict[str, list[str]] = {}
    for device in sorted(configs):
        pair = next((p.devices for p in plan.pairs if device in p.devices), None)
        targets.setdefault("+".join(pair) if pair else device, []).append(device)
    for target, devices in sorted(targets.items()):
        results = [routing_objects(configs[d], await importer.exclusions(slug, d)) for d in devices]
        found, differs = common([r.routing for r in results])
        found, dropped = drop_dangling(found, DeviceRouting())
        for text in sorted({u for r in results for u in r.unmanaged}) + differs + dropped:
            plan.misfit(target, f"routing: {text}")
        if not found.is_empty():
            await importer.store.set_device(slug, target, found)
