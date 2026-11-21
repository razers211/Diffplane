"""A leaf's role from the GUI: leaf <-> border-leaf, a pair together.

Refused towards border-leaf while either switch has segments on a port (a border leaf only
routes), for a centralized fabric's gateway pair (it must stay border-leaf), and towards leaf where
routing intent uses a segment VPN instance that only the border role puts on the switch. Spines
keep their role. Re-seeding keeps the GUI's role (the seed sets a role only on a new device).
"""

from orchestrator.routing_intent import RoutingStore, vpns_on
from orchestrator.segments import SegmentError, _cf, _ref_id

ROLES = ("leaf", "border-leaf")


async def change_role(nb, name: str, role: str) -> dict:
    """Give the device (and its M-LAG peer) the role; the names changed and the roles before."""
    if role not in ROLES:
        raise SegmentError(f"a leaf's role is leaf or border-leaf, not {role!r}")
    device = await nb.find_one("dcim/devices", name=name)
    if device is None:
        raise SegmentError(f"no device {name!r}")
    before = (device.get("role") or {}).get("slug")
    if before not in ROLES:
        raise SegmentError(f"{name} is a {before}: only leaves change role")
    fabric = (device.get("site") or {}).get("slug")
    by_id = {d["id"]: d for d in await nb.find("dcim/devices", site=fabric)}
    peer = by_id.get(_ref_id(_cf(device, "mlag_peer")))
    pair = [device] + ([peer] if peer else [])
    names = sorted(d["name"] for d in pair)
    if before == role:
        return {"devices": names, "role": role, "changed": False}

    store = RoutingStore(nb)
    gateways = await store.gateways(fabric)
    if gateways is not None and set(names) & gateways:
        raise SegmentError(f"{' + '.join(names)} is the fabric's gateway pair: it stays border-leaf")
    if role == "border-leaf":
        for d in pair:
            for iface in await nb.find("dcim/interfaces", device_id=d["id"]):
                if _cf(iface, "segments"):
                    raise SegmentError(f"{d['name']} {iface['name']} carries segments: a border leaf only routes; "
                                       "detach them first")
    elif gateways is None:
        await _check_routing(store, fabric, set(names))
    role_obj = await nb.find_one("dcim/device-roles", slug=role)
    if role_obj is None:
        raise SegmentError(f"no device role {role!r} in NetBox (seed once)")
    for d in pair:
        await nb.update("dcim/devices", d["id"], {"role": role_obj["id"]})
    return {"devices": names, "role": role, "changed": True}


async def _check_routing(store: RoutingStore, fabric: str, leaving: set[str]) -> None:
    """Becoming plain leaves takes every routed segment's VPN instance off them unless attached
    or firewalled there: refuse when routing intent uses one of those there."""
    document = await store.load(fabric)
    targets = {t: r.vpn_uses() for t, r in document.devices.items()}
    targets = {t: uses for t, uses in targets.items() if uses}
    if not targets:
        return
    services, segments = await store.fabric_state(fabric)
    borders = await store.borders(fabric)
    for target, uses in targets.items():
        for leaf in await store.leaves(target):
            if leaf not in leaving:
                continue
            gone = vpns_on(leaf, services, segments, None, borders) - vpns_on(leaf, services, segments, None, borders - leaving)
            for vpn in sorted(gone & set(uses)):
                raise SegmentError(f"as a leaf, {leaf} would lose VPN instance {vpn}, which the routing intent of "
                                   f"{target} uses ({', '.join(uses[vpn])}); change that intent first")
