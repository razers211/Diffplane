"""What an imported fabric may keep the way it was found.

Only an import creates a fabric's overrides document (config context `overrides-<site>`); a fabric
without one is fully derived. This catalogue lists every key the generators read from it, in plain
words. `standard` says whether "Make it standard" may delete it: what is False has no derived form
to go to (the management network, things left alone on a switch) or needs a change by hand first.
"""

import asyncio
from dataclasses import dataclass

from orchestrator import documents
from orchestrator.redact import redact
from orchestrator.segments import SegmentError


@dataclass(frozen=True)
class Override:
    key: str
    scope: str        # fabric, switch, segment or service
    area: str         # the GUI's group: Underlay, Segments, Services or Switch extras
    generator: str    # the generator reading it
    derived: str      # what the platform writes without it
    text: str         # what it means, in plain words
    standard: bool = True


CATALOGUE = (
    Override("dfs_group", "fabric", "Underlay", "Mlag", "DFS group 1", "M-LAG uses another DFS group number."),
    Override("dfs_consistency_check", "fabric", "Underlay", "Mlag", "no check configured", "M-LAG runs a configuration consistency check."),
    Override("dfs_authentication", "fabric", "Underlay", "Mlag", "a DFS key", "M-LAG pairs have no DFS key."),
    Override("bypass_vlan_name", "fabric", "Underlay", "MlagBypass", "a description", "The bypass VLAN has a name instead of a description."),
    Override("ospf_process", "fabric", "Underlay", "Ospf", "OSPF process 1", "OSPF runs as another process number."),
    Override("ospf_bfd_all_interfaces", "fabric", "Underlay", "Ospf", "BFD per link at default timers", "OSPF enables BFD on all interfaces with its own timers."),
    Override("ospf_lsa_arrival", "fabric", "Underlay", "Ospf", "default timer", "OSPF has its own LSA arrival timer."),
    Override("link_mtu", "fabric", "Underlay", "FabricLinks", "MTU 9216", "Fabric links have another MTU, or none set."),
    Override("link_ospf_bfd", "fabric", "Underlay", "Ospf", "BFD on each link", "Fabric links have no BFD of their own."),
    Override("link_hold_max_cost", "fabric", "Underlay", "Ospf", "none", "Fabric links hold max cost after coming up."),
    Override("reflect_clients", "fabric", "Underlay", "BgpEvpn", "spines reflect to leaves", "Spines do not reflect routes to their peers."),
    Override("snmp", "fabric", "Underlay", "Snmp", "the platform manages SNMP", "SNMP is left as the switches have it.", standard=False),
    Override("untagged_subif", "fabric", "Segments", "L2Subinterfaces", "<port>.1", "Untagged sub-interfaces are numbered after the bridge domain."),

    Override("dfs_priority", "switch", "Underlay", "Mlag", "lower hostname wins", "This switch keeps its own DFS priority."),
    Override("heartbeat", "switch", "Underlay", "Mlag", "between the loopbacks", "The M-LAG heartbeat runs over the management addresses."),
    Override("nve_mac", "switch", "Underlay", "Nve", "derived from the VTEP", "The pair keeps its own NVE MAC."),
    Override("evpn_peers", "switch", "Underlay", "BgpEvpn", "the switches it is cabled to", "This switch peers with other route reflectors than those it is cabled to."),
    Override("traffic_segment_enable", "switch", "Switch extras", "Microsegmentation", "off until the fabric turns it on", "`traffic-segment enable` stays on this switch."),
    Override("descriptions", "switch", "Switch extras", "FabricLinks", "`to <switch> <port>`", "Links keep the descriptions they were found with."),
    Override("manual_trunks", "switch", "Switch extras", "Trunks", "`mode lacp-static`", "These Eth-Trunks stay in manual mode."),
    Override("vlan_trunks", "switch", "Switch extras", "Trunks", "L2 sub-interfaces", "These ports stay VLAN trunks for services and carry no segments.", standard=False),
    Override("segments_without_ports", "switch", "Switch extras", "BridgeDomains", "only where a port or gateway needs it", "Bridge domains stay here with no port and no gateway."),
    Override("kept_vpns", "switch", "Switch extras", "TenantVrfs", "removed", "VPN instances bound to nothing are left alone.", standard=False),
    Override("kept_vnis", "switch", "Switch extras", "Nve", "removed", "VNIs with no bridge domain are left alone.", standard=False),
    Override("kept_vlans", "switch", "Switch extras", "Vlans", "removed", "VLANs nothing uses are left alone.", standard=False),
    Override("management", "switch", "Switch extras", "BridgeDomains", "none", "The management network over the fabric is never touched.", standard=False),

    Override("vrf", "segment", "Segments", "TenantVrfs", "<name>-<bd>", "The VPN instance keeps its own name."),
    Override("rd", "segment", "Segments", "TenantVrfs", "<LoopBack0>:<bd>", "One route distinguisher on every switch."),
    Override("l2_rt", "segment", "Segments", "BridgeDomains", "<ASN>:<VNI>", "The bridge domain keeps its own route target."),
    Override("l3_vni", "segment", "Segments", "TenantVrfs", "VNI + 1,000,000", "The VPN instance keeps its own L3 VNI."),
    Override("l3_rt", "segment", "Segments", "TenantVrfs", "<ASN>:<L3 VNI>", "The VPN instance keeps its own route target."),
    Override("gateway_mac", "segment", "Segments", "Gateways", "the fabric's gateway MAC", "This gateway keeps its own MAC."),
    Override("anycast", "segment", "Segments", "Gateways", "anycast gateway", "This gateway is not anycast."),
    Override("arp_broadcast_detect", "segment", "Segments", "Gateways", "off", "This gateway detects ARP broadcasts."),
    Override("arp_collect_host", "segment", "Segments", "Gateways", "off in a centralized fabric", "This gateway collects ARP hosts."),
    Override("bd_arp", "segment", "Segments", "BridgeDomains", "off", "Layer-2 switches proxy ARP in this bridge domain."),

    Override("rd", "service", "Services", "TenantVrfs", "<LoopBack0>:<VLAN>", "One route distinguisher on every switch."),
    Override("mac", "service", "Services", "Vlanifs", "derived from the service", "The Vlanif keeps its own MAC."),
    Override("arp_proxy", "service", "Services", "Vlanifs", "off", "The Vlanif proxies ARP."),
    Override("shared_main", "service", "Services", "Vlanifs", "each switch's own main address", "The shared address is the main one, each switch's own the M-LAG one."),
    Override("connect_interface", "service", "Services", "FirewallBgp", "none", "The firewall's BGP session is sourced from its Vlanif."),
    Override("bfd", "service", "Services", "FirewallBgp", "BFD on", "The firewall's BGP session has no BFD."),
)

KEYS = {(o.scope, o.key): o for o in CATALOGUE}
SECTIONS = {"fabric": "fabric", "devices": "switch", "segments": "segment", "services": "service"}


def check(document: dict) -> None:
    """Refuses a key the catalogue does not list: the generators would ignore it silently."""
    unknown = sorted({f"{section}.{key}" for section, scope in SECTIONS.items()
                      for values in ([document.get(section) or {}] if scope == "fabric" else (document.get(section) or {}).values())
                      for key in values if (scope, key) not in KEYS})
    unknown += sorted(set(document) - set(SECTIONS))
    if unknown:
        raise SegmentError(f"unknown overrides: {', '.join(unknown)}")


async def _context(nb, site: str) -> dict:
    context = await documents.find(nb, f"overrides-{site}")
    if context is None:
        raise SegmentError(f"{site} keeps nothing as found: the platform built it, or an import found nothing to keep")
    return context


def _document(context: dict | None) -> dict:
    return ((context or {}).get("data") or {}).get("overrides") or {}


def entries(document: dict) -> list[dict]:
    """Every value kept, with its catalogue entry. `path` names it in the API."""
    found = []
    for section, scope in SECTIONS.items():
        groups = {None: document.get(section) or {}} if scope == "fabric" else document.get(section) or {}
        for target, values in groups.items():
            for key, value in values.items():
                o = KEYS[(scope, key)]
                found.append({"path": key if target is None else f"{section}/{target}/{key}", "key": key,
                              "scope": scope, "target": target, "area": o.area, "text": o.text,
                              "derived": o.derived, "standard": o.standard, "value": value})
    return found


def _find(path: str) -> tuple[str, str | None, Override]:
    parts = path.split("/")
    section, target, key = ("fabric", None, parts[0]) if len(parts) == 1 else parts if len(parts) == 3 \
        else ("", None, "")
    override = KEYS.get((SECTIONS.get(section), key)) if (target is None) == (section == "fabric") else None
    if override is None:
        raise SegmentError(f"unknown override: {path}")
    return section, target, override


async def view(nb, site: str) -> dict:
    context = await documents.find(nb, f"overrides-{site}")
    return {"imported": context is not None, "entries": entries(_document(context)),
            "misfits": ((context or {}).get("data") or {}).get("misfits") or {}}


async def _write(nb, context: dict, document: dict) -> None:
    await documents.save(nb, context, {**context["data"], "overrides": document})


async def put(nb, site: str, path: str, value) -> dict:
    section, target, override = _find(path)
    context = await _context(nb, site)
    document = _document(context)
    values = document.setdefault(section, {})
    (values if target is None else values.setdefault(target, {}))[override.key] = value
    await _write(nb, context, document)
    return {"path": path, "value": value}


async def remove(nb, site: str, path: str) -> dict:
    """Make it standard: the value goes, the generators derive it again at the next deploy."""
    section, target, override = _find(path)
    if not override.standard:
        raise SegmentError(f"{override.key} cannot be made standard: {override.text}")
    context = await _context(nb, site)
    document = _document(context)
    values = (document.get(section) or {}) if target is None else (document.get(section) or {}).get(target) or {}
    if override.key not in values:
        raise SegmentError(f"{site} does not keep {path}")
    del values[override.key]
    if target is not None and not values:
        del document[section][target]
    await _write(nb, context, document)
    return {"path": path}


async def preview(nb, annet, site: str, path: str) -> dict[str, str]:
    """Each active switch's patch if `path` were made standard; nothing is saved."""
    from orchestrator.drafts import subprocess_patches
    from orchestrator.netbox_clone import clone

    copy = await clone(nb)
    await remove(copy, site, path)
    devices = [d["name"] for d in await nb.find("dcim/devices", site=site, status="active")]
    configs = dict(zip(devices, await asyncio.gather(*(annet.current(d) for d in devices))))
    patches = await subprocess_patches(site, copy, configs)
    return {d: redact(patches.get(d, "")) for d in devices}


async def restore(nb, site: str, document: dict) -> None:
    context = await documents.find(nb, f"overrides-{site}")
    if context is not None:
        await _write(nb, context, document)
