"""A fabric's NTP servers: set from the GUI once the fabric exists.

They live in the fabric's config context (`fabric-<site>`, keys `ntp_servers` and
`ntp_vpn_instance`), read by the Ntp generator. The seed writes them only for a new fabric
and keeps them on every later run. No servers: the platform manages no NTP lines.
"""

import asyncio
import ipaddress
import re

from orchestrator import documents
from orchestrator.netbox import NetBoxClient
from orchestrator.segments import SegmentError

VPN_NAME = re.compile(r"[A-Za-z0-9_.-]{1,31}")


async def _context(nb: NetBoxClient, site: str) -> dict:
    context = await documents.find(nb, f"fabric-{site}")
    if context is None:
        raise SegmentError(f"no fabric {site!r} (seed it first)")
    return context


async def get_ntp(nb: NetBoxClient, site: str) -> dict:
    fabric = ((await _context(nb, site)).get("data") or {}).get("fabric") or {}
    return {"servers": list(fabric.get("ntp_servers") or []), "vpn_instance": fabric.get("ntp_vpn_instance")}


async def set_ntp(nb: NetBoxClient, site: str, servers: list[str], vpn_instance: str | None) -> dict:
    cleaned = []
    for server in servers:
        try:
            address = str(ipaddress.ip_address(server.strip()))
        except ValueError as exc:
            raise SegmentError(f"NTP server: {exc}") from exc
        if address in cleaned:
            raise SegmentError(f"NTP server {address} is listed twice")
        cleaned.append(address)
    vpn_instance = (vpn_instance or "").strip() or None
    if vpn_instance is not None and not VPN_NAME.fullmatch(vpn_instance):
        raise SegmentError(f"{vpn_instance!r} is not a VPN instance name")
    context = await _context(nb, site)
    data = dict(context.get("data") or {})
    fabric = {k: v for k, v in (data.get("fabric") or {}).items() if k not in ("ntp_servers", "ntp_vpn_instance")}
    fabric["ntp_servers"] = cleaned
    if vpn_instance:
        fabric["ntp_vpn_instance"] = vpn_instance
    await documents.save(nb, context, {**data, "fabric": fabric})
    return {"servers": cleaned, "vpn_instance": vpn_instance}


MAC = re.compile(r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}")


async def _update_fabric(nb: NetBoxClient, site: str, change: dict, drop: tuple[str, ...] = ()) -> None:
    context = await _context(nb, site)
    data = dict(context.get("data") or {})
    fabric = {k: v for k, v in (data.get("fabric") or {}).items() if k not in drop}
    await documents.save(nb, context, {**data, "fabric": {**fabric, **change}})


async def set_snmp(nb: NetBoxClient, site: str, location: str, contact: str) -> dict:
    """SNMP location and contact: set in the GUI; empty removes one. The
    readers, trap targets and v2c switch next to them are kept."""
    current = (((await _context(nb, site)).get("data") or {}).get("fabric") or {}).get("snmp") or {}
    snmp = {k: v for k, v in current.items() if k not in ("location", "contact")}
    for key, value in (("location", location), ("contact", contact)):
        value = value.strip()
        if len(value) > 255 or any(not 32 <= ord(c) < 127 or c in '"?' for c in value):
            raise SegmentError(f"SNMP {key}: up to 255 plain ASCII characters on one line, no quotes or '?'")
        if value:
            snmp[key] = value
    await _update_fabric(nb, site, {"snmp": snmp}, drop=("snmp",))
    return {k: snmp[k] for k in ("location", "contact") if k in snmp}


async def set_gateway_mac(nb: NetBoxClient, site: str, mac: str) -> str:
    """The anycast gateway MAC of every VBDIF of the fabric."""
    mac = mac.strip().lower().replace(":", "").replace("-", "").replace(".", "")
    mac = "-".join(mac[i:i + 4] for i in range(0, len(mac), 4))
    if not MAC.fullmatch(mac):
        raise SegmentError("gateway MAC: 12 hex digits, e.g. 0000-5e00-0101")
    if int(mac[:2], 16) & 1:
        raise SegmentError("gateway MAC: a multicast MAC cannot be a gateway (first byte must be even)")
    await _update_fabric(nb, site, {"gateway_mac": mac}, drop=("gateway_mac",))
    return mac


async def get_settings(nb: NetBoxClient, site: str) -> dict:
    """The fabric's settings for the Fabric page, read only: ASN, address pools, M-LAG
    bypass VLAN, gateway MAC and SNMP, as the seed or New fabric wrote them."""
    from seed.loader import POOLS

    context, sites, vrf = await asyncio.gather(_context(nb, site), nb.find("dcim/sites", slug=site),
                                               nb.find_one("ipam/vrfs", name=f"fabric-{site}"))
    fabric = (context.get("data") or {}).get("fabric") or {}
    name = sites[0]["name"] if sites else site
    prefixes = await nb.find("ipam/prefixes", vrf_id=vrf["id"]) if vrf else []
    pools = {}
    for key, (_, purpose) in POOLS.items():
        found = next((p for p in prefixes if p.get("description") == f"{name} {purpose}"), None)
        if found is not None:
            pools[key] = found["prefix"]
    snmp = fabric.get("snmp") or {}
    return {
        "asn": fabric.get("asn"), "vendor": fabric.get("vendor") or "huawei", "pools": pools, "bypass_vlan": (fabric.get("mlag") or {}).get("bypass_vlan"),
        "gateway_mac": fabric.get("gateway_mac"), "snmp_location": snmp.get("location"), "snmp_contact": snmp.get("contact"),
    }



async def get_netstream(nb: NetBoxClient, site: str) -> dict:
    """NetStream of the fabric: off unless turned on from the Fabric page."""
    setting = (((await _context(nb, site)).get("data") or {}).get("fabric") or {}).get("netstream") or {}
    return {"enabled": bool(setting.get("enabled")), "host": setting.get("host"), "port": setting.get("port")}


async def set_netstream(nb: NetBoxClient, site: str, enabled: bool, host: str | None, port: int | None) -> dict:
    """Turn NetStream on (the collector's IPv4 address and UDP port, reached over MEth in
    `_management_vpn_`) or off; turning it off keeps the address for next time."""
    current = await get_netstream(nb, site)
    host = (host or "").strip() or current["host"]
    port = port or current["port"] or 2055
    if host is not None:
        try:
            address = ipaddress.IPv4Address(host)
        except ValueError as exc:
            raise SegmentError(f"collector address: {exc}") from exc
        if address.is_multicast or address.is_loopback or address.is_unspecified:
            raise SegmentError(f"collector address {host}: a host's unicast address is needed")
        host = str(address)
    if not 1 <= int(port) <= 65535:
        raise SegmentError("collector port: 1 to 65535")
    if enabled and host is None:
        raise SegmentError("give the collector's address to turn NetStream on")
    setting = {"enabled": enabled, "host": host, "port": int(port)}
    await _update_fabric(nb, site, {"netstream": setting}, drop=("netstream",))
    return setting


async def get_syslog(nb: NetBoxClient, site: str) -> dict:
    """Syslog of the fabric: off unless turned on from the Fabric page."""
    setting = (((await _context(nb, site)).get("data") or {}).get("fabric") or {}).get("syslog") or {}
    return {"enabled": bool(setting.get("enabled")), "host": setting.get("host"), "port": setting.get("port"),
            "owned": list(setting.get("owned") or [])}


async def set_syslog(nb: NetBoxClient, site: str, enabled: bool, host: str | None, port: int | None) -> dict:
    """Turn syslog on (the receiver's IPv4 address, the southbound VIP in HA, and its UDP port,
    reached over MEth in `_management_vpn_`) or off; off keeps the address for next time. Every
    address the platform ever wrote stays in `owned`, so its loghost line is removed when syslog
    goes off or moves, while hand-made loghosts are never touched."""
    current = await get_syslog(nb, site)
    host = (host or "").strip() or current["host"]
    port = port or current["port"] or 514
    if host is not None:
        try:
            address = ipaddress.IPv4Address(host)
        except ValueError as exc:
            raise SegmentError(f"receiver address: {exc}") from exc
        if address.is_multicast or address.is_loopback or address.is_unspecified:
            raise SegmentError(f"receiver address {host}: a host's unicast address is needed")
        host = str(address)
    if not 1 <= int(port) <= 65535:
        raise SegmentError("receiver port: 1 to 65535")
    if enabled and host is None:
        raise SegmentError("give the receiver's address to turn syslog on")
    owned = sorted(set(current["owned"]) | ({host} if enabled else set()), key=ipaddress.IPv4Address)
    setting = {"enabled": enabled, "host": host, "port": int(port), "owned": owned}
    await _update_fabric(nb, site, {"syslog": setting}, drop=("syslog",))
    return setting

