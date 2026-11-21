"""Import preview: what importing these switches would give, with nothing
written to NetBox and nothing sent to a switch.

The fabric is assembled from the switches' outputs (orchestrator/fabric_import.py), built
with the real builder's steps in an in-memory NetBox (orchestrator/import_build.py), and our
generators run on it through Annet's pipeline (orchestrator/annet_view.py). For each switch
that was read, the patch Annet would send it is the import's verdict: empty means the
platform reproduces the switch exactly; anything else is shown with the misfits.

It runs in its own process (`python -m orchestrator.import_preview`, request as JSON on
stdin, result as JSON on stdout) because Annet's hardware and rulebook providers can be set
only once per process. The request holds running configs (secrets): it travels only through
the pipe.
"""

import asyncio
import ipaddress
import json
import sys

from orchestrator import import_build
from orchestrator.fabric_import import ImportError_, ImportPlan, assemble
from orchestrator.import_parse import ImportParseError, parse_switch
from orchestrator.memory_netbox import FakeNetBox
from orchestrator.segments import SegmentError


def summary(plan: ImportPlan) -> dict:
    """The plan as JSON, for the GUI."""
    def text(value):
        return None if value is None else str(value)

    return {
        "asn": plan.asn, "architecture": plan.architecture,
        "gateway_pair": list(plan.gateway_pair) if plan.gateway_pair else None,
        "bypass_vlan": plan.bypass_vlan, "gateway_mac": plan.gateway_mac,
        "ntp_servers": list(plan.ntp_servers), "ntp_vpn_instance": plan.ntp_vpn,
        "snmp_location": plan.snmp_location, "snmp_contact": plan.snmp_contact,
        "devices": [{"name": d.name, "model": d.model, "role": d.role, "management": d.management,
                     "planned": d.planned, "loopback": text(d.loopback), "vtep": text(d.vtep), "pip": text(d.pip)}
                    for d in sorted(plan.devices.values(), key=lambda d: d.name)],
        "pairs": [{"devices": list(p.devices), "peer_link": f"Eth-Trunk{p.peer_link_trunk}",
                   "peer_link_members": list(p.peer_link_members),
                   "mlag_trunks": {f"Eth-Trunk{n}": list(m) for n, m in sorted(p.mlag_trunks.items())}}
                  for p in plan.pairs],
        "links": [{"spine": l.spine, "spine_port": l.spine_port, "leaf": l.leaf, "leaf_port": l.leaf_port,
                   "network": text(l.network), "unnumbered": l.unnumbered, "found_by": l.found_by}
                  for l in plan.links],
        "segments": [{"vni": s.vni, "bd_id": s.bd, "name": s.name, "gateway": text(s.gateway),
                      "firewall": s.firewall, "peers": sorted(s.peers)}
                     for s in sorted(plan.segments.values(), key=lambda s: s.vni)],
        "attachments": [{"device": d, "port": p, "vni": v, "vid": vid} for d, p, v, vid in plan.attachments],
        "services": [{"name": s.name, "devices": list(s.devices), "port": s.port, "vlan": s.vlan,
                      "address": str(s.address), "next_hop": s.next_hop, "asn": s.asn, "kind": s.kind}
                     for s in sorted(plan.services.values(), key=lambda s: s.name)],
        "trunks": [{"device": d, "name": f"Eth-Trunk{n}", "members": list(m)} for d, n, m in plan.trunks],
        "pools": plan.pools, "overrides": plan.overrides, "misfits": plan.misfits,
    }


async def _build_and_render(plan: ImportPlan, request: dict, read: dict[str, str]) -> dict[str, str]:
    from orchestrator.annet_view import devices_from_netbox, patch_to

    nb = FakeNetBox()
    result = await import_build.build(nb, plan, request["name"], request.get("pools"), request.get("gateway_mac"),
                                      configs=read)
    devices = await asyncio.to_thread(devices_from_netbox, nb, result["site"])
    return {d.name: patch_to(read[d.name], d) for d in devices if d.name in read}


def preview(request: dict) -> dict:
    """{"name", "pools"?, "gateway_mac"?, "roles"?, "switches": {address: {config, version, lldp}}}
    -> {"plan", "patches"} or {"error"}."""
    switches, read = {}, {}
    try:
        for address, outputs in sorted(request["switches"].items()):
            switch = parse_switch(outputs["config"], outputs.get("version", ""), outputs.get("lldp", ""))
            if switch.name in switches:
                raise ImportError_(f"{address} and {switches[switch.name][1]} both have sysname {switch.name}")
            switches[switch.name] = (switch, address)
            read[switch.name] = outputs["config"]
        plan = assemble(switches, request.get("roles"), request.get("architecture"), request.get("gateway_pair"),
                        request.get("drop"))
    except (ImportParseError, ImportError_, ipaddress.AddressValueError, ValueError) as exc:
        return {"error": str(exc)}
    try:
        patches = asyncio.run(_build_and_render(plan, request, read))
    except (SegmentError, ImportError_) as exc:
        return {"plan": summary(plan), "error": str(exc)}
    return {"plan": summary(plan), "patches": patches}


def main() -> int:
    from annet import hardware, rulebook

    # ANN_CONTEXT_CONFIG_PATH comes from the image (orchestrator/Dockerfile), as for `annet`.
    hardware.hardware_connector.set(hardware.AnnetHardwareProvider)
    rulebook.rulebook_provider_connector.set(rulebook.DefaultRulebookProvider)
    json.dump(preview(json.load(sys.stdin)), sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
