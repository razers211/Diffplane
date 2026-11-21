"""DHCP relay on segment gateways.

A routed segment may relay DHCP to up to 8 servers, each in the segment's own VPN instance, in a
service's, or in a peered segment's. Every leaf where the segment's gateway lives relays from its
own loopback in the segment's VPN instance (`dhcp relay giaddr source-interface`), so the
server's reply comes back to that leaf; Option 82 link-selection tells the server the segment's
subnet. Intent: the segment's custom field `dhcp_relay` = {"servers": [{"address", "service" or
"segment"}], "loopback": n}, the number assigned once from 2 to 1023 and kept while relay is on.
Derived and kept in step by `sync` (called by every segment publish): an interface `LoopBack<n>`
on each gateway leaf with one /32 from the fabric's `relay loopbacks` pool.
"""

import ipaddress

from orchestrator import documents
from orchestrator.segments import SegmentError, _cf, _ref_id, display_name

MAX_SERVERS = 8
FIRST, LAST = 2, 1023          # LoopBack0 and 1 are the switch's own and its VTEP; V300 offers 0-1023
POOL = "relay loopbacks"        # the pool prefix's description: "<fabric name> relay loopbacks"
MARK = "relay loopback of "     # the interface description that says a loopback is the platform's


async def _context(nb, fabric: str) -> dict:
    context = await documents.find(nb, f"fabric-{fabric}")
    return ((context or {}).get("data") or {}).get("fabric") or {}


async def pool(nb, fabric: str) -> dict | None:
    site = await nb.find_one("dcim/sites", slug=fabric)
    vrf = await nb.find_one("ipam/vrfs", name=f"fabric-{fabric}")
    if site is None or vrf is None:
        return None
    return next((p for p in await nb.find("ipam/prefixes", vrf_id=vrf["id"])
                 if p.get("description") == f"{site['name']} {POOL}"), None)


async def set_pool(nb, fabric: str, prefix: str) -> dict:
    """The fabric's relay loopback pool (Fabric page). Refused while loopbacks use the old one."""
    try:
        network = ipaddress.IPv4Network(prefix.strip(), strict=True)
    except ValueError as exc:
        raise SegmentError(f"relay pool: {exc}") from exc
    if not 16 <= network.prefixlen <= 28:
        raise SegmentError("relay pool: a /16 to a /28")
    site = await nb.find_one("dcim/sites", slug=fabric)
    vrf = await nb.find_one("ipam/vrfs", name=f"fabric-{fabric}")
    if site is None or vrf is None:
        raise SegmentError(f"no fabric {fabric!r}")
    current = await pool(nb, fabric)
    if current is not None:
        if ipaddress.ip_network(current["prefix"]) == network:
            return {"prefix": str(network)}
        if any((i.get("description") or "").startswith(MARK) for i in await nb.find("dcim/interfaces", site=fabric)):
            raise SegmentError("relay loopbacks use the current pool: turn relay off on every segment first")
    for other in await nb.find("ipam/prefixes", vrf_id=vrf["id"]):
        if other is not current and ipaddress.ip_network(other["prefix"]).overlaps(network):
            raise SegmentError(f"relay pool {network} overlaps {other['prefix']} ({other.get('description') or 'a prefix'})")
    fields = {"prefix": str(network), "vrf": vrf["id"], "status": "active", "description": f"{site['name']} {POOL}",
              "scope_type": "dcim.site", "scope_id": site["id"]}
    if current is None:
        await nb.create("ipam/prefixes", fields)
    else:
        await nb.update("ipam/prefixes", current["id"], fields)
    return {"prefix": str(network)}


async def own_instance_firewall(nb, fabric: str, segment: dict) -> bool:
    """The segment's firewall sits in the segment's own VPN instance."""
    from generators.fabric import segment_vpn
    from orchestrator.routing import Routing

    firewall = _cf(segment, "firewall")
    instance = segment_vpn(display_name(segment), _cf(segment, "bd_id"))
    return bool(firewall) and any(s["name"] == firewall and s["vrf"] == instance for s in await Routing(nb).services(fabric))


def _server(entry: dict, services: set[str], peers: dict[int, str]) -> dict:
    try:
        address = ipaddress.IPv4Address(str(entry.get("address", "")).strip())
    except ValueError as exc:
        raise SegmentError(f"DHCP server: {exc}") from exc
    if address.is_multicast or address.is_loopback or address.is_unspecified:
        raise SegmentError(f"DHCP server {address}: a host's unicast address is needed")
    service, segment = entry.get("service"), entry.get("segment")
    if service and segment:
        raise SegmentError(f"DHCP server {address}: in a service or in a segment, not both")
    if service and service not in services:
        raise SegmentError(f"DHCP server {address}: behind {service!r}, which is not this segment's firewall")
    if segment is not None and int(segment) not in peers:
        raise SegmentError(f"DHCP server {address}: VNI {segment} is not a segment this one is peered with"
                           " (in a symmetric fabric)")
    return {"address": str(address), **({"service": service} if service else {}),
            **({"segment": int(segment)} if segment is not None else {})}


async def set_relay(nb, fabric: str, vni: int, servers: list[dict]) -> dict:
    """Relay a routed segment's DHCP to `servers` (empty: relay off)."""
    from orchestrator.segments import Segments

    segments = Segments(nb)
    site = await segments._site(fabric)
    l2vpns = {l["identifier"]: l for l in await segments._l2vpns(site)}
    segment = l2vpns.get(vni)
    if segment is None:
        raise SegmentError(f"no segment with VNI {vni} in {fabric}")
    if servers:
        if _ref_id(_cf(segment, "gateway_prefix")) is None:
            raise SegmentError(f"segment {display_name(segment)} is not routed: route it first")
        if len(servers) > MAX_SERVERS:
            raise SegmentError(f"at most {MAX_SERVERS} DHCP servers per segment")
        if await pool(nb, fabric) is None:
            raise SegmentError("set the fabric's relay loopback pool first (Fabric page)")
        firewall = _cf(segment, "firewall")
        # A server behind a service is reached through the segment's default route: its own firewall.
        services = {firewall} if firewall else set()
        if any(s.get("service") for s in servers) and await own_instance_firewall(nb, fabric, segment):
            raise SegmentError(f"{firewall} sits in segment {display_name(segment)}'s own VPN instance: a DHCP server "
                               "behind it is not supported (no route back to the relay loopbacks)")
        peers = {p: display_name(l2vpns[p]) for p in _cf(segment, "peers") or [] if p in l2vpns}
        if await _gateway_pair(nb, fabric) is not None:
            peers = {}   # centralized peering is static routes for the subnets only: no way back
        cleaned = [_server(s, services, peers) for s in servers]
        if len({s["address"] for s in cleaned}) != len(cleaned):
            raise SegmentError("a DHCP server is listed twice")
        number = (_cf(segment, "dhcp_relay") or {}).get("loopback") or await _free_number(nb, site, l2vpns.values())
        value = {"servers": cleaned, "loopback": number}
    elif ((_cf(segment, "ipv6") or {}).get("servers")) and (_cf(segment, "dhcp_relay") or {}).get("loopback"):
        value = {"servers": [], "loopback": _cf(segment, "dhcp_relay")["loopback"]}   # DHCPv6 still relays
    else:
        value = None
    await nb.update("vpn/l2vpns", segment["id"], {"custom_fields": {"dhcp_relay": value}})
    await segments.publish(fabric)   # keeps the relay loopbacks in step (sync)
    return {"vni": vni, "relay": value}


async def _free_number(nb, site: dict, l2vpns) -> int:
    """The lowest loopback number no segment's relay and no switch of the fabric uses."""
    used = {(_cf(l, "dhcp_relay") or {}).get("loopback") for l in l2vpns}
    for iface in await nb.find("dcim/interfaces", site=site["slug"]):
        if iface["name"].startswith("LoopBack") and iface["name"][8:].isdigit():
            used.add(int(iface["name"][8:]))
    number = next((n for n in range(FIRST, LAST + 1) if n not in used), None)
    if number is None:
        raise SegmentError(f"no loopback number left ({FIRST}-{LAST} all in use)")
    return number


async def _gateway_pair(nb, fabric: str) -> set[str] | None:
    """A centralized fabric's gateway pair, where every gateway lives; None: symmetric,
    each segment's gateway lives on the leaves with one of its ports (never a border leaf's)."""
    settings = await _context(nb, fabric)
    return set(settings.get("gateway_pair") or ()) if settings.get("architecture") == "centralized" else None


async def sync(nb, fabric: str, l2vpns: list[dict], attachments) -> tuple[dict[int, dict[str, str]], dict[int, dict[str, str]]]:
    """Make the relay loopbacks in NetBox what the relaying segments need: create the missing
    ones (interface and /32 from the pool), delete the platform's that are no longer needed.
    `attachments`: an async callable giving {vni: [Attachment]}, read only when a segment relays.
    A segment relaying DHCPv6 also gets a /128 on it from the fabric's IPv6 relay /64.
    Returns each relaying segment's IPv4 and IPv6 loopback address per switch, for the context."""
    relaying = {l["identifier"]: l for l in l2vpns
                if _cf(l, "dhcp_relay") and _ref_id(_cf(l, "gateway_prefix")) is not None}
    wanted: dict[tuple[str, str], int] = {}   # (switch, LoopBack<n>) -> segment VNI
    if relaying:
        pair, ports = await _gateway_pair(nb, fabric), await attachments()
        for vni, l in relaying.items():
            hosts = pair if pair is not None else {a.device for a in ports.get(vni, [])}
            for device in hosts:
                wanted[(device, f"LoopBack{_cf(l, 'dhcp_relay')['loopback']}")] = vni
    interfaces = [i for i in await nb.find("dcim/interfaces", site=fabric)
                  if (i.get("description") or "").startswith(MARK)]
    have = {((i.get("device") or {}).get("name"), i["name"]): i for i in interfaces}
    for key, iface in have.items():
        if key not in wanted:
            for ip in await nb.find("ipam/ip-addresses", interface_id=iface["id"]):
                await nb.delete("ipam/ip-addresses", ip["id"])
            await nb.delete("dcim/interfaces", iface["id"])
    addresses: dict[int, dict[str, str]] = {}
    addresses6: dict[int, dict[str, str]] = {}
    needs4 = {vni for vni, l in relaying.items() if (_cf(l, "dhcp_relay") or {}).get("servers")}
    needs6 = {vni for vni, l in relaying.items() if (_cf(l, "ipv6") or {}).get("servers")}
    relay6 = None
    for key, iface in have.items():
        if key not in wanted:
            continue
        for ip in await nb.find("ipam/ip-addresses", interface_id=iface["id"]):
            version = ipaddress.ip_interface(ip["address"]).version
            if version == 4 and wanted[key] in needs4:
                addresses.setdefault(wanted[key], {})[key[0]] = ip["address"]
            elif version == 6 and wanted[key] in needs6:
                addresses6.setdefault(wanted[key], {})[key[0]] = ip["address"]
            else:   # its family no longer relays: the address goes, the loopback stays for the other
                await nb.delete("ipam/ip-addresses", ip["id"])
    devices = None
    for key, vni in sorted(wanted.items()):
        if key not in have:
            if devices is None:
                devices = {d["name"]: d for d in await nb.find("dcim/devices", site=fabric)}
            have[key] = await nb.create("dcim/interfaces", {"device": devices[key[0]]["id"], "name": key[1], "type": "virtual",
                                                            "description": f"{MARK}{display_name(relaying[vni])}"})
    missing = {key: vni for key, vni in wanted.items() if vni in needs4 and key[0] not in addresses.get(vni, {})}
    missing6 = {key: vni for key, vni in wanted.items() if vni in needs6 and key[0] not in addresses6.get(vni, {})}
    if not missing and not missing6:
        return addresses, addresses6
    relay_pool = await pool(nb, fabric) if missing else None
    if missing and relay_pool is None:
        raise SegmentError("the fabric has no relay loopback pool (Fabric page)")
    for (device, name), vni in sorted(missing.items()):
        segment = display_name(relaying[vni])
        iface = have[(device, name)]
        ip = await nb.allocate_ip(relay_pool["id"], {"assigned_object_type": "dcim.interface",
                                                     "assigned_object_id": iface["id"], "status": "active",
                                                     "description": f"{device} {MARK}{segment}"})
        host = f"{ipaddress.ip_interface(ip['address']).ip}/32"
        if ip["address"] != host:
            await nb.update("ipam/ip-addresses", ip["id"], {"address": host})
        addresses.setdefault(vni, {})[device] = host
    if missing6:
        from orchestrator import ipv6

        _, relay6 = await ipv6.prefixes(nb, fabric)
        if relay6 is None:
            raise SegmentError("the fabric has no IPv6 pool (Fabric page)")
    for (device, name), vni in sorted(missing6.items()):
        iface = have[(device, name)]
        ip = await nb.allocate_ip(relay6["id"], {"assigned_object_type": "dcim.interface", "assigned_object_id": iface["id"],
                                                 "status": "active", "description": f"{device} {MARK}{display_name(relaying[vni])}"})
        host = f"{ipaddress.ip_interface(ip['address']).ip}/128"
        if ip["address"] != host:
            await nb.update("ipam/ip-addresses", ip["id"], {"address": host})
        addresses6.setdefault(vni, {})[device] = host
    return addresses, addresses6
