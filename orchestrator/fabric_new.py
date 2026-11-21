"""A new fabric from the GUI.

The wizard sends the same settings as a fabric YAML (`fabric:` and `services:`) and a first
batch of switches in the Add switches format. The settings are checked by the seed's own
model (`seed.model.parse`), the switches by the builder (orchestrator/fabric_build.py), all
before the first write; then the builder creates the site, ASN, contexts, VRF and pools with
the seed's steps, followed by the switches. The fabric lives in NetBox only: no YAML is
written, and the seed never touches it.
"""

import ipaddress
import re

from orchestrator import documents
from orchestrator.fabric_build import FabricBuilder
from orchestrator.netbox import NetBoxClient
from orchestrator.segments import SegmentError
from seed.model import Fabric, FabricError, parse

PRIVATE = [ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,49}\Z")
RESERVED = {"inventory"}
# east's split of its /16: four /24 pools at the start, gateways the upper half.
SMALL_POOLS = ("loopback", "vtep", "p2p", "mlag_bypass")


def address_plan(supernet: str) -> dict[str, str]:
    """A proposed split of one private supernet into the fabric's pools (editable in the GUI)."""
    try:
        net = ipaddress.IPv4Network(supernet)
    except ValueError as exc:
        raise SegmentError(f"invalid subnet: {exc}") from exc
    if not any(net.subnet_of(p) for p in PRIVATE):
        raise SegmentError(f"{net} is not private address space (10/8, 172.16/12, 192.168/16)")
    if not 8 <= net.prefixlen <= 21:
        raise SegmentError(f"give a /8 to /21 subnet to split (got /{net.prefixlen})")
    lower, upper = net.subnets(prefixlen_diff=1)
    small = list(lower.subnets(new_prefix=24))
    plan = {**{key: str(small[i]) for i, key in enumerate(SMALL_POOLS)}, "gateways": str(upper)}
    # The PIP layout (owner, 2026-11-19: the heartbeat must not ride the bypass route over the
    # peer-link): a /24 of its own, or on a /21 (four /24s) the bypass /24's upper half.
    if len(small) > len(SMALL_POOLS):
        plan["pip"] = str(small[len(SMALL_POOLS)])
    else:
        plan["mlag_bypass"], plan["pip"] = map(str, ipaddress.IPv4Network(plan["mlag_bypass"]).subnets(prefixlen_diff=1))
    return plan


async def proposed_mac(netbox: NetBoxClient) -> str:
    """A locally administered gateway MAC no other fabric uses (east took 0200-00fa-0001)."""
    used = set()
    for context in await documents.every(netbox):
        mac = (((context.get("data") or {}).get("fabric") or {}).get("gateway_mac"))
        if mac:
            used.add(mac)
    n = 1
    while f"0200-00fa-{n:04x}" in used:
        n += 1
    return f"0200-00fa-{n:04x}"


def definition(settings: dict) -> Fabric:
    """The wizard's settings as a fabric definition, checked like a fabric YAML."""
    name = str(settings.get("name") or "").strip()
    if not NAME.match(name):
        raise SegmentError("a fabric name is 1-50 letters, digits, spaces, '_' or '-'")
    ntp = settings.get("ntp_servers") or []
    data = {
        "fabric": {
            "name": name,
            "asn": settings.get("asn"),
            "architecture": settings.get("architecture") or "symmetric",
            "prefixes": dict(settings.get("prefixes") or {}),
            "mlag": {"bypass_vlan": settings.get("bypass_vlan")},
            "gateway_mac": settings.get("gateway_mac"),
        },
        "services": {
            "ntp_servers": ntp,
            **({"ntp_vpn_instance": settings["ntp_vpn_instance"]} if ntp and settings.get("ntp_vpn_instance") else {}),
            "snmp": {k: v for k, v in (("location", settings.get("snmp_location")),
                                       ("contact", settings.get("snmp_contact"))) if v},
        },
    }
    for key in ("loopback", "vtep", "p2p", "mlag_bypass", "gateways"):
        if not data["fabric"]["prefixes"].get(key):
            raise SegmentError(f"the address plan needs a {key} pool")
    try:
        fabric = parse(data, settings_only=True)
    except FabricError as exc:
        raise SegmentError(str(exc)) from exc
    for key, pool in (("loopback", fabric.loopback_prefix), ("vtep", fabric.vtep_prefix), ("p2p", fabric.p2p_prefix),
                      ("mlag_bypass", fabric.mlag_bypass_prefix), ("gateways", fabric.gateways_prefix),
                      ("pip", fabric.pip_prefix)):
        if pool is None:
            continue   # pip: the PIP layout only (an imported fabric)
        if not any(pool.subnet_of(p) for p in PRIVATE):
            raise SegmentError(f"{key} pool {pool} is not private address space")
        if pool.prefixlen > 30:
            raise SegmentError(f"{key} pool {pool} is too small")
    if fabric.gateways_prefix.prefixlen > 24:
        raise SegmentError(f"gateways pool {fabric.gateways_prefix} is smaller than one /24 segment subnet")
    return fabric


async def create(netbox: NetBoxClient, settings: dict, switches: dict, imported: bool = False,
                 detected: dict | None = None) -> dict:
    """Check the settings and the first switches, then write the fabric and the switches.
    `imported`: read from its switches (orchestrator/import_build.py)."""
    fabric = definition(settings)
    if fabric.slug in RESERVED:
        raise SegmentError(f"{fabric.slug!r} is reserved")
    for site in await netbox.find("dcim/sites"):
        if site["slug"] == fabric.slug or (site.get("name") or "").lower() == fabric.name.lower():
            raise SegmentError(f"a site named {site.get('name') or site['slug']} already exists")
    if await documents.find(netbox, f"fabric-{fabric.slug}"):
        raise SegmentError(f"fabric-{fabric.slug} already exists")
    result = await FabricBuilder(netbox, fabric.slug, new=fabric, imported=imported).add(switches, detected)
    return {"site": fabric.slug, **result}
