"""IPv6 for routed segments.

One IPv6 pool per fabric (Fabric page): its first /64 is kept for the relay loopbacks' /128s (a
child prefix, so segment allocations skip it), the rest gives one /64 per segment (NetBox next
available), gateway `::1`. Per routed segment (custom fields on its L2VPN): `gateway_prefix6`, its
/64, and `ipv6` = {"hosts": "slaac" (default) | "dhcpv6" | "none", "servers": [...]}: DHCPv6
servers, as for DHCP relay (own VPN instance, behind the segment's firewall, in a peered segment), relayed
through the segment's relay loopback, which then also carries a /128 (orchestrator/dhcp_relay.py).

Services: optional IPv6 typed in like IPv4 (custom field `service_ipv6` on the
service's VRF = {"address": the Vlanif's, "next_hop": the firewall's or ISP router's}).
"""

import ipaddress

from orchestrator.segments import SegmentError, _cf, _ref_id, display_name

POOL = "IPv6 gateways"            # "<fabric name> IPv6 gateways"
RELAY = "IPv6 relay loopbacks"    # its first /64: "<fabric name> IPv6 relay loopbacks"
HOSTS = ("slaac", "dhcpv6", "none")


async def _site_vrf(nb, fabric: str) -> tuple[dict, dict]:
    site = await nb.find_one("dcim/sites", slug=fabric)
    vrf = await nb.find_one("ipam/vrfs", name=f"fabric-{fabric}")
    if site is None or vrf is None:
        raise SegmentError(f"no fabric {fabric!r}")
    return site, vrf


async def prefixes(nb, fabric: str) -> tuple[dict | None, dict | None]:
    """The fabric's IPv6 pool and its relay /64, or None for each not set."""
    try:
        site, vrf = await _site_vrf(nb, fabric)
    except SegmentError:
        return None, None
    found = await nb.find("ipam/prefixes", vrf_id=vrf["id"])
    pick = lambda what: next((p for p in found if p.get("description") == f"{site['name']} {what}"), None)
    return pick(POOL), pick(RELAY)


async def set_pool(nb, fabric: str, prefix: str) -> dict:
    """Set the fabric's IPv6 pool (a /32 to a /56); refused while a segment uses the old one."""
    try:
        network = ipaddress.IPv6Network(prefix.strip(), strict=True)
    except ValueError as exc:
        raise SegmentError(f"IPv6 pool: {exc}") from exc
    if not 32 <= network.prefixlen <= 56:
        raise SegmentError("IPv6 pool: a /32 to a /56")
    if network.is_multicast or network.is_link_local or network.is_loopback:
        raise SegmentError("IPv6 pool: ULA (fd00::/8) or global unicast space")
    site, vrf = await _site_vrf(nb, fabric)
    pool, relay = await prefixes(nb, fabric)
    if pool is not None and ipaddress.ip_network(pool["prefix"]) == network:
        return {"prefix": str(network), "relay": relay["prefix"] if relay else None}
    if pool is not None:
        users = [l for l in await nb.find("vpn/l2vpns", type="vxlan-evpn") if _cf(l, "gateway_prefix6")]
        if users:
            raise SegmentError(f"segments use the IPv6 pool ({', '.join(display_name(l) for l in users)}): "
                               "turn their IPv6 off first")
    for other in await nb.find("ipam/prefixes", vrf_id=vrf["id"]):
        net = ipaddress.ip_network(other["prefix"])
        if other not in (pool, relay) and net.version == 6 and net.overlaps(network):
            raise SegmentError(f"IPv6 pool {network} overlaps {other['prefix']}")
    fields = {"vrf": vrf["id"], "status": "active", "scope_type": "dcim.site", "scope_id": site["id"]}
    first = next(network.subnets(new_prefix=64))
    if pool is None:
        await nb.create("ipam/prefixes", {**fields, "prefix": str(network), "description": f"{site['name']} {POOL}"})
        await nb.create("ipam/prefixes", {**fields, "prefix": str(first), "description": f"{site['name']} {RELAY}"})
    else:
        await nb.update("ipam/prefixes", pool["id"], {"prefix": str(network)})
        await nb.update("ipam/prefixes", relay["id"], {"prefix": str(first)})
    return {"prefix": str(network), "relay": str(first)}


def service_ipv6(address: str, next_hop: str, leaves: list[str] | None = None,
                 addresses: dict[str, str] | None = None) -> dict:
    """A service's IPv6: the Vlanif subnet (the platform picks ::1) or its address with the length,
    and the next hop, another host of that subnet. On an M-LAG pair (`leaves`, sorted), as for IPv4:
    the subnet (::1 and ::2 for the leaves in name order, ::3 the shared M-LAG address) or the shared
    address with each leaf's address in `addresses`."""
    try:
        text = str(address).strip()
        network = ipaddress.IPv6Network(text, strict=False)
        iface = ipaddress.IPv6Interface(text)
        hop = ipaddress.IPv6Address(str(next_hop).strip())
        mains = {k: ipaddress.IPv6Interface(str(v).strip()) for k, v in (addresses or {}).items()}
    except ValueError as exc:
        raise SegmentError(f"service IPv6: {exc}") from exc
    if not 64 <= network.prefixlen <= 126:
        raise SegmentError("service IPv6: the Vlanif subnet is a /64 to a /126")
    if network.is_multicast or network.is_link_local or network.is_loopback:
        raise SegmentError("service IPv6: ULA (fd00::/8) or global unicast space")
    given_subnet = iface.ip == network.network_address
    first = lambda n: ipaddress.IPv6Interface(f"{network.network_address + n}/{network.prefixlen}")
    if leaves is None:
        if mains:
            raise SegmentError("service IPv6: per-leaf addresses are for a service on an M-LAG pair")
        iface = first(1) if given_subnet else iface
        hosts = {iface.ip}
    elif mains:
        if set(mains) != set(leaves):
            raise SegmentError(f"service IPv6: give one address for each of {' and '.join(leaves)}")
        if given_subnet or any(m.network != network for m in mains.values()):
            raise SegmentError("service IPv6: the shared M-LAG address and each leaf's address, in one subnet")
        hosts = {iface.ip, *(m.ip for m in mains.values())}
        if len(hosts) != 3 or network.network_address in hosts:
            raise SegmentError("service IPv6: the three addresses must be distinct hosts of the subnet")
    else:
        if not given_subnet:
            raise SegmentError("service IPv6: on an M-LAG pair give the Vlanif subnet, or the shared M-LAG "
                               "address with each leaf's address")
        mains = {leaves[0]: first(1), leaves[1]: first(2)}
        iface = first(3)
        hosts = {iface.ip, *(m.ip for m in mains.values())}
    if hop not in network or hop in hosts or hop == network.network_address:
        raise SegmentError("service IPv6: the next hop must be another host in the Vlanif subnet")
    result = {"address": str(iface), "next_hop": str(hop)}
    if leaves is not None:
        result["addresses"] = {k: str(v) for k, v in sorted(mains.items())}
    return result


def _server(entry: dict, services: set[str], peers: set[int]) -> dict:
    try:
        address = ipaddress.IPv6Address(str(entry.get("address", "")).strip())
    except ValueError as exc:
        raise SegmentError(f"DHCPv6 server: {exc}") from exc
    if address.is_multicast or address.is_loopback or address.is_unspecified or address.is_link_local:
        raise SegmentError(f"DHCPv6 server {address}: a host's global or ULA address is needed")
    service, segment = entry.get("service"), entry.get("segment")
    if service and service not in services:
        raise SegmentError(f"DHCPv6 server {address}: behind {service!r}, which is not this segment's firewall "
                           "with IPv6")
    if segment is not None and int(segment) not in peers:
        raise SegmentError(f"DHCPv6 server {address}: VNI {segment} is not a peered segment with IPv6 "
                           "(in a symmetric fabric)")
    return {"address": str(address), **({"service": service} if service else {}),
            **({"segment": int(segment)} if segment is not None else {})}


async def set_ipv6(nb, fabric: str, vni: int, enabled: bool, hosts: str = "slaac", servers: list[dict] | None = None) -> dict:
    """Turn IPv6 on for a routed segment (its /64 from the fabric pool), with its hosts mode and
    DHCPv6 servers, or off (the /64 released)."""
    from orchestrator import dhcp_relay
    from orchestrator.segments import Segments

    segments = Segments(nb)
    site = await segments._site(fabric)
    l2vpns = {l["identifier"]: l for l in await segments._l2vpns(site)}
    segment = l2vpns.get(vni)
    if segment is None:
        raise SegmentError(f"no segment with VNI {vni} in {fabric}")
    prefix_id = _ref_id(_cf(segment, "gateway_prefix6"))
    relay = _cf(segment, "dhcp_relay") or {}
    if not enabled:
        from orchestrator.routing_intent import RoutingStore

        def off(services, segments):
            return services, [{**x, "routing": {**x["routing"], "ipv6": None}} if x["vni"] == vni and x["routing"] else x
                              for x in segments]

        from generators.fabric import segment_vpn
        from orchestrator.routing import Routing

        instance = segment_vpn(display_name(segment), _cf(segment, "bd_id"))
        if users := [s["name"] for s in await Routing(nb).services(fabric) if s["vrf"] == instance and s.get("ipv6")]:
            raise SegmentError(f"service {', '.join(users)} in {display_name(segment)}'s VPN instance has IPv6: "
                               "take it away first")
        await RoutingStore(nb).guard(fabric, f"turning IPv6 off on {display_name(segment)}", off)
        fields = {"gateway_prefix6": None, "ipv6": None}
        if relay and not relay.get("servers"):
            fields["dhcp_relay"] = None   # the loopback was IPv6's alone
        await nb.update("vpn/l2vpns", segment["id"], {"custom_fields": fields})
        if prefix_id is not None:
            await nb.delete("ipam/prefixes", prefix_id)
        await segments.publish(fabric)
        return {"vni": vni, "ipv6": None}
    if _ref_id(_cf(segment, "gateway_prefix")) is None:
        raise SegmentError(f"segment {display_name(segment)} is not routed: route it first")
    if hosts not in HOSTS:
        raise SegmentError(f"hosts is one of {', '.join(HOSTS)}")
    servers = servers or []
    if servers and hosts != "dhcpv6":
        raise SegmentError("DHCPv6 servers need the hosts mode dhcpv6")
    if len(servers) > dhcp_relay.MAX_SERVERS:
        raise SegmentError(f"at most {dhcp_relay.MAX_SERVERS} DHCPv6 servers per segment")
    pool, relay_prefix = await prefixes(nb, fabric)
    if pool is None:
        raise SegmentError("set the fabric's IPv6 pool first (Fabric page)")
    # A server behind the segment's firewall needs that service in IPv6 too; peered segments need IPv6.
    services: set[str] = set()
    if firewall := _cf(segment, "firewall"):
        from orchestrator.routing import Routing

        if any(f["name"] == firewall and f.get("ipv6") for f in await Routing(nb).services(fabric)):
            services = {firewall}
    peers = {p for p in _cf(segment, "peers") or [] if p in l2vpns and _cf(l2vpns[p], "gateway_prefix6")}
    if await dhcp_relay._gateway_pair(nb, fabric) is not None:
        peers = set()
    if any(s.get("service") for s in servers) and await dhcp_relay.own_instance_firewall(nb, fabric, segment):
        raise SegmentError(f"{firewall} sits in segment {display_name(segment)}'s own VPN instance: a DHCPv6 server "
                           "behind it is not supported (no route back to the relay loopback)")
    cleaned = [_server(s, services, peers) for s in servers]
    if len({s["address"] for s in cleaned}) != len(cleaned):
        raise SegmentError("a DHCPv6 server is listed twice")
    fields = {"ipv6": {"hosts": hosts, "servers": cleaned}}
    if prefix_id is None:
        child = await nb.allocate_prefix(pool["id"], {
            "prefix_length": 64, "status": "active", "scope_type": "dcim.site", "scope_id": site["id"],
            "description": f"{fabric}:{display_name(segment)} IPv6 gateway"})
        fields["gateway_prefix6"] = child["id"]
    if cleaned and not relay.get("loopback"):
        fields["dhcp_relay"] = {"servers": relay.get("servers") or [], "loopback": await dhcp_relay._free_number(
            nb, site, l2vpns.values())}
    elif not cleaned and relay and not relay.get("servers"):
        fields["dhcp_relay"] = None
    await nb.update("vpn/l2vpns", segment["id"], {"custom_fields": fields})
    await segments.publish(fabric)
    return {"vni": vni, "ipv6": fields["ipv6"]}
