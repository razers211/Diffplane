"""Deleting a fabric: admins only, every switch planned, no deploy running,
the fabric's name typed to confirm.

Everything of the fabric in NetBox goes: cables, its addressing (the VRF `fabric-<site>` with its
prefixes and addresses), segments, services, its config contexts (fabric, segments, routing,
overrides), switches (with their ports), racks and locations, the site, and its ASN when no other
fabric uses it. Nothing is sent to any switch; the platform's history (deploys, checks, audit) stays.
A fabric with a YAML in seed/fabrics comes back at the next seed: the plan says so.
"""

from pathlib import Path

from orchestrator import documents
from orchestrator.routing_intent import status_of
from orchestrator.segments import SegmentError, _cf, _ref_id

FABRICS = Path(__file__).resolve().parents[1] / "seed" / "fabrics"
CONTEXTS = ("fabric", "segments", "routing", "overrides")


async def plan(nb, slug: str) -> dict:
    """What deleting the fabric would remove, and why it cannot be deleted yet (if so)."""
    site = await nb.find_one("dcim/sites", slug=slug)
    if site is None:
        raise SegmentError(f"no fabric {slug!r}")
    devices = await nb.find("dcim/devices", site_id=site["id"])
    vrf = await nb.find_one("ipam/vrfs", name=f"fabric-{slug}")
    prefixes = await nb.find("ipam/prefixes", vrf_id=vrf["id"]) if vrf else []
    addresses = await nb.find("ipam/ip-addresses", vrf_id=vrf["id"]) if vrf else []
    segments = [l for l in await nb.find("vpn/l2vpns") if _ref_id(_cf(l, "fabric")) == site["id"]]
    services = [v for v in await nb.find("ipam/vrfs") if _ref_id(_cf(v, "fabric")) == site["id"] and _cf(v, "service_device")]
    cables = set()
    for device in devices:
        for iface in await nb.find("dcim/interfaces", device_id=device["id"]):
            if iface.get("cable"):
                cables.add(_ref_id(iface["cable"]))
    active = sorted(d["name"] for d in devices if status_of(d) == "active")
    yaml = next((p.name for p in FABRICS.glob("*.yaml") if p.stem == slug), None)
    return {
        "fabric": slug, "name": site["name"],
        "devices": sorted(d["name"] for d in devices), "active": active,
        "cables": len(cables), "segments": len(segments), "services": len(services),
        "prefixes": len(prefixes), "addresses": len(addresses),
        "yaml": yaml,
        "blocker": f"decommission first: {', '.join(active)} {'is' if len(active) == 1 else 'are'} active" if active else None,
    }


async def delete(nb, slug: str, confirm: str) -> dict:
    """Delete the fabric from NetBox. The caller has checked that no deploy runs."""
    summary = await plan(nb, slug)
    if summary["blocker"]:
        raise SegmentError(summary["blocker"])
    if confirm.strip() not in (slug, summary["name"]):
        raise SegmentError(f"type the fabric's name ({slug}) to delete it")
    site = await nb.find_one("dcim/sites", slug=slug)
    devices = await nb.find("dcim/devices", site_id=site["id"])
    # Cables first (they hold two devices together), then what points at the fabric, then the site.
    for device in devices:
        for iface in await nb.find("dcim/interfaces", device_id=device["id"]):
            if iface.get("cable") and await nb.find("dcim/cables", id=_ref_id(iface["cable"])):
                await nb.delete("dcim/cables", _ref_id(iface["cable"]))
    vrf = await nb.find_one("ipam/vrfs", name=f"fabric-{slug}")
    if vrf is not None:
        for ip in await nb.find("ipam/ip-addresses", vrf_id=vrf["id"]):
            await nb.delete("ipam/ip-addresses", ip["id"])
        for prefix in sorted(await nb.find("ipam/prefixes", vrf_id=vrf["id"]), key=lambda p: -int(p["prefix"].split("/")[1])):
            await nb.delete("ipam/prefixes", prefix["id"])   # children before their pools
    for l2vpn in [l for l in await nb.find("vpn/l2vpns") if _ref_id(_cf(l, "fabric")) == site["id"]]:
        await nb.delete("vpn/l2vpns", l2vpn["id"])
    for service in [v for v in await nb.find("ipam/vrfs") if _ref_id(_cf(v, "fabric")) == site["id"]]:
        await nb.delete("ipam/vrfs", service["id"])
    if vrf is not None and await nb.find("ipam/vrfs", id=vrf["id"]):
        await nb.delete("ipam/vrfs", vrf["id"])
    for name in CONTEXTS:
        context = await documents.find(nb, f"{name}-{slug}")
        if context is not None:
            await documents.remove(nb, context)
    for device in devices:
        await nb.delete("dcim/devices", device["id"])          # its ports and their addresses go with it
    for rack in await nb.find("dcim/racks", site_id=site["id"]):
        await nb.delete("dcim/racks", rack["id"])
    for location in await nb.find("dcim/locations", site_id=site["id"]):
        await nb.delete("dcim/locations", location["id"])
    asns = [_ref_id(a) for a in site.get("asns") or []]
    await nb.delete("dcim/sites", site["id"])
    for asn_id in asns:   # the fabric's ASN, unless another fabric uses the same number
        if not any(asn_id in [_ref_id(a) for a in s.get("asns") or []] for s in await nb.find("dcim/sites")):
            await nb.delete("ipam/asns", asn_id)
    return summary
