"""Routing intent of a fabric: firewall services and routed segments.

Design: one VPN instance per routed segment, VBDIF anycast gateway;
segments reach each other by explicit peering (route-target import) and/or through a
firewall service: a VPN instance on the leaf the firewall is cabled to, reached over a
plain Vlanif, with inter-VPN static routes. New routed segments use the fabric's first
firewall service for their default route unless told otherwise.

Stored in NetBox (the orchestrator is the only writer):
  service  VRF "<site>:<NAME>", custom fields fabric, service_device, service_port,
           service_vlan, service_address, service_next_hop, service_asn (optional),
           service_kind ("firewall", the default, or "isp":, container only, its BGP
           session is a routing-intent peer with policies), service_addresses (a service on an
           M-LAG pair: each leaf's main Vlanif address; service_address is then the shared
           M-LAG address), service_ipv6 (optional: {address, next_hop}),
           service_vrf (optional: the VPN instance it sits in when not its
           own: {"name": <another service's instance>, "rd_vlan": <that instance's RD VLAN>}, or
           {"name": <a routed segment's instance>})
  routing  segment (L2VPN) custom fields gateway_prefix (allocated from the fabric's
           gateway pool), peers (VNIs), firewall (service name)
"""

import asyncio
import ipaddress
import re

from generators.fabric import segment_vpn, v6
from orchestrator import documents
from orchestrator.netbox import NetBoxClient
from orchestrator.segments import SegmentError, Segments, _cf, _ref_id, display_name, name_error

KINDS = ("firewall", "isp")
SERVICE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,30}")
RESERVED = ("_management_vpn_", "_public_")
GATEWAY_POOL = "segment gateway subnets"   # description the seed gives the pool


class Routing:
    def __init__(self, netbox: NetBoxClient):
        self.nb = netbox
        self.segments = Segments(netbox)

    # -- firewall services --------------------------------------------------------------

    async def _service_vrfs(self, site: dict) -> list[dict]:
        return [v for v in await self.nb.find("ipam/vrfs")
                if _ref_id(_cf(v, "fabric")) == site["id"] and _cf(v, "service_device")]

    async def services(self, fabric: str) -> list[dict]:
        site = await self.segments._site(fabric)
        found = await asyncio.gather(*(self._service_json(v) for v in await self._service_vrfs(site)))
        return sorted(found, key=lambda s: s["name"])

    async def _service_json(self, vrf: dict) -> dict:
        from orchestrator.routing_intent import RoutingStore

        device = _cf(vrf, "service_device")
        found = [d for d in await self.nb.find("dcim/devices") if d["id"] == _ref_id(device)]
        name = found[0]["name"] if found else None
        addresses = _cf(vrf, "service_addresses")
        # A service on an M-LAG server trunk is the pair's: on both leaves.
        peer = await RoutingStore(self.nb).peer_of(found[0]) if found and addresses else None
        joined = _cf(vrf, "service_vrf") or {}
        return {
            "name": display_name(vrf),
            # Its VPN instance: its own (named after it), or the one it joined.
            "vrf": joined.get("name") or display_name(vrf),
            # The VLAN of its instance's RD (<LoopBack0>:<VLAN>); none in a segment's instance.
            "rd_vlan": joined.get("rd_vlan") if joined.get("name") else _cf(vrf, "service_vlan"),
            "device": name,
            "port": _cf(vrf, "service_port"),
            "vlan": _cf(vrf, "service_vlan"),
            "address": _cf(vrf, "service_address"),
            "next_hop": _cf(vrf, "service_next_hop"),
            "asn": _cf(vrf, "service_asn"),
            "kind": _cf(vrf, "service_kind") or "firewall",
            "devices": sorted((name, peer)) if peer else [name],
            "addresses": addresses,
            "ipv6": _cf(vrf, "service_ipv6"),
        }

    async def create_service(self, fabric: str, name: str, device: str, port: str, vlan: int,
                             address: str, next_hop: str, asn: int | None = None, kind: str = "firewall",
                             addresses: dict[str, str] | None = None, ipv6: dict | None = None,
                             vrf: str | None = None) -> dict:
        """A service on a leaf's dedicated physical port or single trunk (that leaf's alone),
        or on an M-LAG pair's dedicated server trunk (both leaves, M-LAG dual-active gateway: `address` is then the subnet, or the
        shared M-LAG address with each leaf's address in `addresses`)."""
        site = await self.segments._site(fabric)
        if kind not in KINDS:
            raise SegmentError(f"a service is one of {', '.join(KINDS)}")
        if kind == "isp" and asn is not None:
            raise SegmentError("an ISP service has no ASN: its BGP session is a routing-intent peer with policies")
        await self._check_asn(site, asn)
        if not SERVICE_NAME.fullmatch(name) or re.search(r"-\d+$", name) or name in RESERVED:
            # A segment's VPN instance is <name>-<bd_id>: a service's never ends that way.
            raise SegmentError("a service name is 1-31 letters, digits, '-' or '_', starts with a letter, "
                               "does not end in -<digits> (that is a segment's VPN instance) and is not a "
                               "system VPN instance")
        if any(s["name"] == name for s in await self.services(fabric)):
            raise SegmentError(f"{fabric} already has a service named {name!r}")
        if not 2 <= vlan <= 4094:
            raise SegmentError("VLAN must be 2-4094")
        bypass = await self._bypass_vlan(site)
        if vlan == bypass:
            raise SegmentError(f"VLAN {vlan} is the fabric's M-LAG bypass VLAN")
        # Route distinguishers are <LoopBack0>:<bd_id> and <LoopBack0>:<service VLAN>.
        if any(_cf(l, "bd_id") == vlan for l in await self.segments._l2vpns(site)):
            raise SegmentError(f"VLAN {vlan} equals a bridge-domain ID of {fabric}; their route distinguishers would clash")
        try:
            hop = ipaddress.IPv4Address(next_hop)
        except ValueError as exc:
            raise SegmentError(f"invalid address: {exc}") from exc
        dev, port_obj = await self.segments._port(device, port, site)
        from orchestrator.routing_intent import RoutingStore

        store = RoutingStore(self.nb)
        gateways = await store.gateways(fabric)
        if gateways is not None and device not in gateways:
            raise SegmentError(f"{fabric} is centralized: services sit on its gateway pair "
                               f"({' and '.join(sorted(gateways))}), not on {device}")
        borders = await store.borders(fabric)
        if borders and device not in borders:
            # once a symmetric fabric has border leaves, new services go
            # there; services made before stay where they are.
            raise SegmentError(f"{fabric} has border leaves ({', '.join(sorted(borders))}): "
                               f"services sit on them, not on {device}")
        if (port_obj.get("type") or {}).get("value") == "lag" and _is_mlag(port_obj):
            devices, iface, addresses = await self._on_pair(fabric, dev, port_obj, name, vlan, address, addresses, hop)
        else:
            if addresses:
                raise SegmentError("per-leaf addresses are for a service on an M-LAG pair's M-LAG trunk")
            devices = [device]
            try:
                iface = ipaddress.IPv4Interface(address)
            except ValueError as exc:
                raise SegmentError(f"invalid address: {exc}") from exc
            if iface.network.prefixlen > 30 or hop not in iface.network or hop in (
                    iface.ip, iface.network.network_address, iface.network.broadcast_address):
                raise SegmentError("the next hop must be another host in the Vlanif subnet (/30 or larger)")
            if _cf(port_obj, "segments"):
                raise SegmentError(f"{device} {port} carries segments; a service port must be dedicated")
        six = await self._ipv6(fabric, ipv6, devices if len(devices) > 1 else None) if ipv6 else None
        others = await self.services(fabric)
        for other in others:
            # A port may carry several services,, each in its own VLAN.
            shared = set(devices) & set(other["devices"])
            if shared and vlan in (other["vlan"], other["rd_vlan"]):
                raise SegmentError(f"VLAN {vlan} is already used by service {other['name']} on "
                                   f"{' and '.join(sorted(shared))}"
                                   + ("" if other["vlan"] == vlan else " (its VPN instance's route distinguisher)"))
        joined = await self._joined(fabric, name, vrf, kind, asn, devices, six, others)
        if joined:
            await self._vrf_field()
        await self.nb.create("ipam/vrfs", {
            "name": f"{fabric}:{name}", "enforce_unique": False,
            "description": f"{fabric} {kind} service {name} (device VPN instance)",
            "custom_fields": {"fabric": site["id"], "service_device": dev["id"], "service_port": port,
                              "service_vlan": vlan, "service_address": str(iface), "service_next_hop": str(hop),
                              "service_asn": asn, "service_kind": kind, "service_addresses": addresses,
                              "service_ipv6": six, **({"service_vrf": joined} if joined else {})},
        })
        await self.segments.publish(fabric)
        return {"name": name, "device": device, "port": port, "vlan": vlan, "address": str(iface),
                "next_hop": str(hop), "asn": asn, "kind": kind, "devices": devices, "addresses": addresses,
                "ipv6": six, "vrf": (joined or {}).get("name") or name,
                "rd_vlan": joined.get("rd_vlan") if joined else vlan}

    async def _joined(self, fabric: str, name: str, vrf: str | None, kind: str, asn: int | None,
                      devices: list[str], six: dict | None, others: list[dict]) -> dict | None:
        """The VPN instance a new service joins, as stored in `service_vrf`;
        None: its own. Another service's instance keeps its RD VLAN; on a switch that does not
        have it yet (e.g. a second ISP link on the other border spine) the service creates it
        there too, RD <that switch's LoopBack0>:<RD VLAN>, so no other service there may use
        that VLAN. A routed segment's instance is the segment's own."""
        instance = (vrf or "").strip() or name
        sharing = [s for s in others if s["vrf"] == instance]
        if sharing:
            rd_vlan = sharing[0]["rd_vlan"]
            there = {d for s in sharing for d in s["devices"]}
            for other in others:
                new = (set(devices) - there) & set(other["devices"])
                if new and other["vrf"] != instance and rd_vlan in (other["vlan"], other["rd_vlan"]):
                    raise SegmentError(f"VPN instance {instance}'s route distinguisher uses VLAN {rd_vlan}, which "
                                       f"service {other['name']} already uses on {' and '.join(sorted(new))}")
            return {"name": instance, "rd_vlan": rd_vlan}
        if instance == name:
            return None
        from orchestrator.routing_intent import RoutingStore, segment_vpn

        _, segments = await RoutingStore(self.nb).fabric_state(fabric)
        segment = next((s for s in segments if s.get("routing") and s.get("bd_id") is not None
                        and segment_vpn(s["name"], s["bd_id"]) == instance), None)
        if segment is None:
            raise SegmentError(f"{fabric} has no service or routed segment with VPN instance {instance!r}")
        if six and not segment["routing"].get("ipv6"):
            raise SegmentError(f"segment {segment['name']} has no IPv6: turn it on before giving the service IPv6")
        if asn is not None and kind == "firewall" and await RoutingStore(self.nb).gateways(fabric) is not None:
            # A centralized fabric's segment instance has no BGP family to advertise into the session.
            raise SegmentError("in a centralized fabric a firewall in a segment's VPN instance has no BGP session: "
                               "leave its ASN empty")
        return {"name": instance}

    async def _vrf_field(self) -> None:
        """The `service_vrf` custom field, created on first use (installs seeded before it)."""
        if await self.nb.find_one("extras/custom-fields", name="service_vrf") is None:
            await self.nb.create("extras/custom-fields", {
                "name": "service_vrf", "label": "Service vrf", "type": "json", "object_types": ["ipam.vrf"],
                "description": "VPN instance a service sits in when not its own: another service's or a segment's"})

    async def _in_segment(self, fabric: str, service: dict) -> dict | None:
        """The routed segment whose VPN instance holds this service, if any."""
        if service["vrf"] == service["name"]:
            return None
        from orchestrator.routing_intent import RoutingStore, segment_vpn

        _, segments = await RoutingStore(self.nb).fabric_state(fabric)
        return next((s for s in segments if s.get("routing") and s.get("bd_id") is not None
                     and segment_vpn(s["name"], s["bd_id"]) == service["vrf"]), None)

    async def _ipv6(self, fabric: str, ipv6: dict, leaves: list[str] | None) -> dict:
        """A service's IPv6 checked; `leaves`: a pair service's two leaves, sorted."""
        from orchestrator import ipv6 as v6

        six = v6.service_ipv6(ipv6.get("address", ""), ipv6.get("next_hop", ""), leaves, ipv6.get("addresses"))
        pool, _ = await v6.prefixes(self.nb, fabric)
        if pool is not None and ipaddress.ip_network(six["address"], strict=False).overlaps(
                ipaddress.ip_network(pool["prefix"])):
            raise SegmentError(f"service IPv6 {six['address']} is inside the fabric's IPv6 pool {pool['prefix']}")
        return six

    async def set_service_ipv6(self, fabric: str, name: str, ipv6: dict | None) -> dict:
        """Give a service IPv6 ({address, next_hop}) or take it away (None)."""
        site = await self.segments._site(fabric)
        vrf = next((v for v in await self._service_vrfs(site) if display_name(v) == name), None)
        if vrf is None:
            raise SegmentError(f"{fabric} has no service {name!r}")
        if ipv6 is None:
            users = [display_name(l) for l in await self.segments._l2vpns(site)
                     if any(s.get("service") == name for s in (_cf(l, "ipv6") or {}).get("servers") or [])]
            if users:
                raise SegmentError(f"DHCPv6 servers of {', '.join(users)} sit behind {name}; move them first")
            await self._guard(fabric, f"removing the IPv6 of service {name}", lambda services, segments: (
                [{**x, "ipv6": None} if x["name"] == name else x for x in services], segments))
            six = None
        else:
            service = await self._service_json(vrf)
            if (segment := await self._in_segment(fabric, service)) is not None and not segment["routing"].get("ipv6"):
                raise SegmentError(f"service {name} sits in segment {segment['name']}'s VPN instance, which has no IPv6")
            six = await self._ipv6(fabric, ipv6, service["devices"] if service["addresses"] else None)
        updated = await self.nb.update("ipam/vrfs", vrf["id"], {"custom_fields": {"service_ipv6": six}})
        await self.segments.publish(fabric)
        return await self._service_json(updated)

    async def _on_pair(self, fabric: str, dev: dict, port: dict, name: str, vlan: int, address: str,
                       addresses: dict[str, str] | None, hop: ipaddress.IPv4Address):
        """A service on an M-LAG server trunk: (both leaves, the M-LAG address, each leaf's address).

        The trunk is dedicated on both leaves (no segments, no other service); the Vlanif is the
        M-LAG dual-active gateway (for services only)."""
        from pydantic import ValidationError

        from orchestrator.routing_intent import PairGateway, RoutingStore

        store = RoutingStore(self.nb)
        peer = await store.peer_of(dev)
        if peer is None:
            raise SegmentError(f"a service on an Eth-Trunk is an M-LAG pair's; {dev['name']} has no M-LAG peer")
        devices = sorted((dev["name"], peer))
        mirror = await self.segments._mirror(dev, port)
        for leaf, trunk in [(dev, port)] + ([mirror] if mirror else []):
            if _cf(trunk, "segments"):
                raise SegmentError(f"{leaf['name']} {trunk['name']} carries segments; a service trunk must be dedicated")
        try:
            if addresses:
                gateway = PairGateway(vlan=vlan, trunk=port["name"], addresses=addresses, mlag_address=address)
            else:
                gateway = PairGateway(vlan=vlan, trunk=port["name"], subnet=address)
        except ValidationError as exc:
            raise SegmentError("on an M-LAG pair give the Vlanif subnet (e.g. 10.100.101.0/29), or the shared M-LAG "
                               "address with each leaf's address: " + "; ".join(e["msg"] for e in exc.errors())) from exc
        gateway = gateway.with_addresses(devices)
        if set(gateway.addresses) != set(devices):
            raise SegmentError(f"give one address for each of {' and '.join(devices)}")
        net = gateway.mlag_address.network
        if hop not in net or hop in {net.network_address, net.broadcast_address, gateway.mlag_address.ip,
                                     *(a.ip for a in gateway.addresses.values())}:
            raise SegmentError("the next hop must be another host in the Vlanif subnet, not a leaf's or the "
                               "M-LAG address")
        return devices, gateway.mlag_address, {k: str(v) for k, v in sorted(gateway.addresses.items())}

    async def set_service_asn(self, fabric: str, name: str, asn: int | None) -> dict:
        """Set or clear the firewall's ASN (None: no BGP session with the firewall)."""
        site = await self.segments._site(fabric)
        vrf = next((v for v in await self._service_vrfs(site) if display_name(v) == name), None)
        if vrf is None:
            raise SegmentError(f"{fabric} has no service {name!r}")
        if asn is not None and (_cf(vrf, "service_kind") or "firewall") == "isp":
            raise SegmentError("an ISP service has no ASN: its BGP session is a routing-intent peer with policies")
        await self._check_asn(site, asn)
        if asn is not None and await self._in_segment(fabric, await self._service_json(vrf)) is not None:
            from orchestrator.routing_intent import RoutingStore

            if await RoutingStore(self.nb).gateways(fabric) is not None:
                raise SegmentError("in a centralized fabric a firewall in a segment's VPN instance has no BGP session")
        updated = await self.nb.update("ipam/vrfs", vrf["id"], {"custom_fields": {"service_asn": asn}})
        await self.segments.publish(fabric)
        return await self._service_json(updated)

    async def _check_asn(self, site: dict, asn: int | None) -> None:
        if asn is None:
            return
        if not 1 <= asn <= 4294967295:
            raise SegmentError("the firewall ASN must be 1-4294967295")
        ctx = await documents.find(self.nb, f"fabric-{site['slug']}")
        if asn == (((ctx or {}).get("data") or {}).get("fabric") or {}).get("asn"):
            raise SegmentError("the firewall ASN must differ from the fabric ASN (the session is eBGP)")

    async def delete_service(self, fabric: str, name: str) -> None:
        site = await self.segments._site(fabric)
        vrf = next((v for v in await self._service_vrfs(site) if display_name(v) == name), None)
        if vrf is None:
            raise SegmentError(f"{fabric} has no service {name!r}")
        users = [display_name(l) for l in await self.segments._l2vpns(site) if _cf(l, "firewall") == name]
        if users:
            raise SegmentError(f"service {name} is the firewall of {', '.join(users)}; change them first")
        await self._guard(fabric, f"deleting service {name}",
                          lambda services, segments: ([s for s in services if s["name"] != name], segments))
        service = await self._service_json(vrf)
        await self.nb.delete("ipam/vrfs", vrf["id"])
        if service["device"] and service["kind"] == "firewall":
            # Its inter-VPN routes are routing objects: owned until a verified deploy removed them,
            # on each leaf it was on. (An ISP service has none: its routing is intent, retired with it.)
            from orchestrator.routing_intent import RoutingStore, status_of

            # Retired by its VPN instance and next hops: the instance may stay for other services
            #, so its session and routes go by those (generators.fabric.retired_firewalls).
            entry = " ".join([service["vrf"], service["next_hop"],
                              *([v6(ipaddress.ip_address(service["ipv6"]["next_hop"]))] if service.get("ipv6") else [])])
            for device in service["devices"]:
                # A planned leaf never had it deployed (and is never verified).
                if status_of(await self.nb.find_one("dcim/devices", name=device)) == "active":
                    await RoutingStore(self.nb).retire_service(fabric, device, entry)
        await self.segments.publish(fabric)

    async def _no_hosted(self, fabric: str, name: str, bd_id: int | None, what: str) -> None:
        """Refuse a change that would take away a segment's VPN instance while services sit in it."""
        if bd_id is None:
            return
        instance = segment_vpn(name, bd_id)
        if users := [s["name"] for s in await self.services(fabric) if s["vrf"] == instance and s["name"] != instance]:
            raise SegmentError(f"service {', '.join(users)} sits in segment {name}'s VPN instance {instance}: "
                               f"delete it before {what}")

    async def _bypass_vlan(self, site: dict) -> int | None:
        ctx = await documents.find(self.nb, f"fabric-{site['slug']}")
        return (((ctx or {}).get("data") or {}).get("fabric") or {}).get("mlag", {}).get("bypass_vlan")

    # -- routed segments ----------------------------------------------------------------

    async def set_routing(self, fabric: str, vni: int, routed: bool, prefix_length: int = 24,
                          firewall: str | None = "default", peers: list[int] | None = None,
                          subnet: str | None = None, chosen: bool = False) -> dict:
        """Make a segment routed (gateway, firewall, peers) or not routed.

        firewall="default" picks the fabric's first service; None means
        no firewall default route. `subnet` (a rollback): the exact gateway subnet to take
        back, instead of the next free one; refused unless it lies in the gateway pool and
        nothing else in the fabric overlaps it. `chosen`: the subnet is the operator's own,
        inside the pool or outside it, refused only where the fabric's addresses overlap it.
        """
        site = await self.segments._site(fabric)
        l2vpns = {l["identifier"]: l for l in await self.segments._l2vpns(site)}
        segment = l2vpns.get(vni)
        if segment is None:
            raise SegmentError(f"no segment with VNI {vni} in {fabric}")

        if not routed:
            await self._no_hosted(fabric, display_name(segment), _cf(segment, "bd_id"), "making it not routed")
            await self._guard(fabric, f"making segment {display_name(segment)} not routed",
                              lambda services, segments: (services, [{**s, "routing": None} if s["vni"] == vni else s
                                                                     for s in segments]))
            for other in l2vpns.values():
                if vni in (_cf(other, "peers") or []):
                    await self._set_cf(other, peers=[p for p in _cf(other, "peers") if p != vni] or None)
            if (prefix := _ref_id(_cf(segment, "gateway_prefix"))) is not None:
                prefix6 = _ref_id(_cf(segment, "gateway_prefix6"))
                await self._set_cf(segment, gateway_prefix=None, peers=None, firewall=None, dhcp_relay=None,   # relay off
                                   gateway_prefix6=None, ipv6=None)                                          # IPv6 off
                if prefix6 is not None:
                    await self.nb.delete("ipam/prefixes", prefix6)
                await self.nb.delete("ipam/prefixes", prefix)
            await self.segments.publish(fabric)
            return {"vni": vni, "routed": False}

        if (error := name_error(display_name(segment), _cf(segment, "bd_id"))) is not None:
            raise SegmentError(f"rename the segment before routing it: {error}")
        # Only firewall services carry segments' default routes; ISP access is decided later.
        everyone = await self.services(fabric)
        services = [s["name"] for s in everyone if s["kind"] == "firewall"]
        if firewall == "default":
            firewall = _cf(segment, "firewall") or (services[0] if services else None)
        if firewall is not None and firewall not in services:
            raise SegmentError(f"{fabric} has no firewall service {firewall!r}")
        if firewall is not None:
            chosen = next(s for s in everyone if s["name"] == firewall)
            own = segment_vpn(display_name(segment), _cf(segment, "bd_id"))
            if chosen["vrf"] != own and (hosting := await self._in_segment(fabric, chosen)) is not None:
                # Its routes would be inter-VPN routes between two segment instances.
                raise SegmentError(f"firewall {firewall} sits in segment {hosting['name']}'s VPN instance: it can only "
                                   f"be that segment's firewall; peer the segments instead")
        peers = sorted(set(peers or []) - {vni})
        for peer in peers:
            if peer not in l2vpns:
                raise SegmentError(f"no segment with VNI {peer} in {fabric}")
            if _cf(l2vpns[peer], "gateway_prefix") is None:
                raise SegmentError(f"segment {display_name(l2vpns[peer])} is not routed; route it first")
        if not 8 <= prefix_length <= 30:
            raise SegmentError("gateway prefix length must be 8-30")

        current = _cf(segment, "firewall")
        if current and firewall != current:
            behind = [srv["address"] for srv in ((_cf(segment, "dhcp_relay") or {}).get("servers") or [])
                      + ((_cf(segment, "ipv6") or {}).get("servers") or []) if srv.get("service") == current]
            if behind:
                raise SegmentError(f"DHCP servers {', '.join(behind)} of {display_name(segment)} sit behind {current}; "
                                   "move them before changing its firewall")

        def new_firewall(services, segments):
            return services, [{**s, "routing": {**s["routing"], "firewall": firewall}}
                              if s["vni"] == vni and s["routing"] else s for s in segments]

        await self._guard(fabric, f"changing the firewall of segment {display_name(segment)}", new_firewall)

        gateway = _ref_id(_cf(segment, "gateway_prefix"))
        try:
            wanted = ipaddress.ip_interface(subnet).network if subnet else None
        except ValueError as exc:
            raise SegmentError(f"{subnet!r} is not an IPv4 network (e.g. 10.20.30.0/24)") from exc
        if wanted is not None and (wanted.version != 4 or not 8 <= wanted.prefixlen <= 30):
            raise SegmentError(f"{wanted}: a gateway subnet is an IPv4 network from /8 to /30")
        if gateway is not None and wanted is not None:
            current = (await self.nb.find("ipam/prefixes", id=gateway))[0]["prefix"]
            if ipaddress.ip_network(current) != wanted:
                raise SegmentError(f"segment {display_name(segment)} is routed with {current}; "
                                   f"make it not routed before taking {wanted}")
        stretched = None
        if gateway is None and wanted is None:
            #.1 (): a segment stretched to another DC takes the gateway subnet it has there.
            from orchestrator import dci

            stretched = await dci.peer_gateway(self.nb, fabric, vni)
        if gateway is None:
            pool = await self._gateway_pool(site)
            fields = {"status": "active", "scope_type": "dcim.site", "scope_id": site["id"],
                      "description": f"{fabric}:{display_name(segment)} gateway"}
            if stretched is not None:
                other, wanted = stretched
                fields["description"] += f" (stretched, the same as in {other})"
                child = await self._exact_prefix(pool, wanted, fields, inside_pool=False)
            elif wanted is None:
                child = await self.nb.allocate_prefix(pool["id"], {"prefix_length": prefix_length, **fields})
            else:
                child = await self._exact_prefix(pool, wanted, fields, inside_pool=not chosen)
            gateway = child["id"]

        # Peering is symmetric: both VPN instances import each other's L3 route target.
        before = set(_cf(segment, "peers") or [])
        for peer in set(peers) - before:
            other = l2vpns[peer]
            await self._set_cf(other, peers=sorted(set(_cf(other, "peers") or []) | {vni}))
        for peer in before - set(peers):
            if peer in l2vpns:
                other = l2vpns[peer]
                await self._set_cf(other, peers=[p for p in _cf(other, "peers") or [] if p != vni] or None)
        await self._set_cf(segment, gateway_prefix=gateway, peers=peers or None, firewall=firewall)
        await self.segments.publish(fabric)
        return {"vni": vni, "routed": True, "firewall": firewall, "peers": peers}

    async def _guard(self, fabric: str, what: str, change) -> None:
        from orchestrator.routing_intent import RoutingStore

        await RoutingStore(self.nb).guard(fabric, what, change)

    async def _gateway_pool(self, site: dict) -> dict:
        vrf = await self.nb.find_one("ipam/vrfs", name=f"fabric-{site['slug']}")
        pools = [p for p in await self.nb.find("ipam/prefixes", vrf_id=vrf["id"] if vrf else None)
                 if (p.get("description") or "").endswith(GATEWAY_POOL)]
        if not pools:
            raise SegmentError(f"{site['slug']} has no gateway pool: add prefixes.gateways to its fabric YAML and seed")
        return pools[0]

    async def _exact_prefix(self, pool: dict, wanted, fields: dict, inside_pool: bool = True) -> dict:
        """The gateway subnet a rollback asks back: only inside the gateway pool and only if
        no prefix of the fabric's namespace overlaps it, except the pool and what contains it.
        Nothing is computed: NetBox's own prefixes say whether it is taken. A stretched segment's
        subnet comes from the other fabric's pool, so it may lie outside this one's."""
        pool_net = ipaddress.ip_network(pool["prefix"])
        if inside_pool and not wanted.subnet_of(pool_net):
            raise SegmentError(f"{wanted} is outside the fabric's gateway pool {pool_net}")
        vrf_id = _ref_id(pool.get("vrf"))
        for prefix in await self.nb.find("ipam/prefixes", vrf_id=vrf_id):
            net = ipaddress.ip_network(prefix["prefix"])
            if net.overlaps(wanted) and not pool_net.subnet_of(net):
                raise SegmentError(f"{wanted} is no longer free: {net} ({prefix.get('description') or 'no description'}) "
                                   "overlaps it")
        return await self.nb.create("ipam/prefixes", {"prefix": str(wanted), "vrf": vrf_id, **fields})

    async def _set_cf(self, l2vpn: dict, **values) -> None:
        await self.nb.update("vpn/l2vpns", l2vpn["id"], {"custom_fields": values})
        l2vpn.setdefault("custom_fields", {}).update(values)


def _is_mlag(iface: dict) -> bool:
    """An Eth-Trunk bound to its pair's M-LAG (NetBox tag `m-lag`)."""
    return any(t.get("slug") == "m-lag" for t in iface.get("tags") or [] if isinstance(t, dict))
