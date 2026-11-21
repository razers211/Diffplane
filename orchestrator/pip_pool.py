"""The PIP layout for a fabric that has none yet (owner, 2026-11-19): each M-LAG switch gets its own
PIP on LoopBack1 from a `pip` pool and the pair's VTEP moves to LoopBack2 (generators/fabric.py
pip_layout). The bypass route then goes to the peer's PIP, so the M-LAG heartbeat (LoopBack0 to
LoopBack0) runs through the spines instead of over the peer-link. Lone leaves keep their VTEP on
LoopBack1. New fabrics are proposed one from the start (orchestrator/fabric_new.py)."""

import ipaddress

from orchestrator import documents
from orchestrator.dhcp_relay import MARK
from orchestrator.segments import SegmentError
from seed.loader import POOLS


async def set_pool(nb, fabric: str, prefix: str) -> dict:
    from orchestrator.fabric_build import FabricBuilder
    from orchestrator.fabric_ntp import _update_fabric

    try:
        network = ipaddress.IPv4Network(prefix.strip(), strict=True)
    except ValueError as exc:
        raise SegmentError(f"PIP pool: {exc}") from exc
    if not 16 <= network.prefixlen <= 28:
        raise SegmentError("PIP pool: a /16 to a /28")
    site = await nb.find_one("dcim/sites", slug=fabric)
    vrf = await nb.find_one("ipam/vrfs", name=f"fabric-{fabric}")
    context = await documents.find(nb, f"fabric-{fabric}")
    if site is None or vrf is None or context is None:
        raise SegmentError(f"no fabric {fabric!r}")
    current = ((context.get("data") or {}).get("fabric") or {}).get("pip_prefix")
    if current:
        if ipaddress.ip_network(current) == network:
            return {"prefix": str(network), "switches": []}
        raise SegmentError(f"the fabric already has the PIP layout, with pool {current}")
    for other in await nb.find("ipam/prefixes", vrf_id=vrf["id"]):
        if ipaddress.ip_network(other["prefix"]).overlaps(network):
            raise SegmentError(f"PIP pool {network} overlaps {other['prefix']} ({other.get('description') or 'a prefix'})")

    paired = sorted(d["name"] for d in await nb.find("dcim/devices", site_id=site["id"])
                    if (d.get("custom_fields") or {}).get("mlag_peer"))
    ifaces = {(i["device"]["name"], i["name"]): i for i in await nb.find("dcim/interfaces", site=fabric)
              if i["name"] in ("LoopBack1", "LoopBack2")}
    relayed = [n for n in paired if (ifaces.get((n, "LoopBack2"), {}).get("description") or "").startswith(MARK)]
    if relayed:   # the PIP layout puts the VTEP on LoopBack2, where relay loopbacks start today
        raise SegmentError(f"{', '.join(relayed)} use LoopBack2 for DHCP relay: turn relay off on every segment first")
    vteps = {}
    for name in paired:
        lb1 = ifaces.get((name, "LoopBack1"))
        found = await nb.find("ipam/ip-addresses", interface_id=lb1["id"], role="anycast") if lb1 else []
        if found:
            vteps[name] = found
    if not vteps:
        raise SegmentError("the fabric has no M-LAG pair with a VTEP: nothing would use a PIP")

    await nb.create("ipam/prefixes", {"prefix": str(network), "vrf": vrf["id"], "status": POOLS["pip"][0],
                                      "description": f"{site['name']} {POOLS['pip'][1]}",
                                      "scope_type": "dcim.site", "scope_id": site["id"]})
    builder = FabricBuilder(nb, fabric)
    await builder._load()   # finds the new pool as prefix:pip
    for name, ips in vteps.items():
        lb2 = await builder._interface(name, "LoopBack2")
        for ip in ips:   # the same VTEP address, so tunnels keep their endpoints
            await nb.update("ipam/ip-addresses", ip["id"], {"assigned_object_type": "dcim.interface",
                                                            "assigned_object_id": lb2["id"]})
        await builder._loopback0(name, "LoopBack1", "pip")
    # Last: generators read the new layout only once this is set (intent writes are refused
    # while a deploy rolls out, so none can take a half-written layout).
    await _update_fabric(nb, fabric, {"pip_prefix": str(network)})
    return {"prefix": str(network), "switches": sorted(vteps)}
