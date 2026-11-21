"""Topology graph: NetBox cabling checked against LLDP from the collector.

Every NetBox cable becomes a link. Each end on a device the collector polled recently
is checked against the LLDP neighbours seen on that interface:

  ok          exactly the expected peer (device name and port) is seen
  missing     no neighbour is seen
  mismatch    something else is seen

A link takes the worst status of its checked ends; with no checked end it is
"unverified" (planned devices, or the collector has no fresh data). Neighbours seen on
an interface without a NetBox cable become "unexpected" links.
"""

import re
from dataclasses import dataclass, field

Port = tuple[str, str]  # (device, interface)

_SEVERITY = {"ok": 0, "missing": 1, "mismatch": 2}


def port_key(name: str) -> tuple:
    """Natural order: 10GE1/0/2 before 10GE1/0/10."""
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"(\d+)", name))


@dataclass(frozen=True)
class Device:
    name: str
    site: str | None
    role: str | None
    status: str | None


@dataclass
class Telemetry:
    # (device, interface) -> [(neighbour system name, neighbour port id)]
    lldp: dict[Port, list[Port]] = field(default_factory=dict)
    oper_up: dict[Port, bool] = field(default_factory=dict)
    # devices with a recent successful poll
    collected: set[str] = field(default_factory=set)


def _end(port: Port, peer: Port, telemetry: Telemetry) -> dict:
    device, interface = port
    seen = telemetry.lldp.get(port, [])
    if device not in telemetry.collected:
        check = "unverified"
    elif not seen:
        check = "missing"
    elif seen == [peer]:
        check = "ok"
    else:
        check = "mismatch"
    return {
        "device": device,
        "interface": interface,
        "oper_up": telemetry.oper_up.get(port),
        "lldp": [{"system_name": s, "port_id": p} for s, p in seen],
        "check": check,
    }


def build(devices: list[Device], cables: list[tuple[Port, Port]], telemetry: Telemetry) -> dict:
    nodes = {
        d.name: {
            "name": d.name,
            "site": d.site,
            "role": d.role,
            "status": d.status,
            "in_netbox": True,
            "collected": d.name in telemetry.collected,
        }
        for d in devices
    }

    # Cables can lead to devices outside the requested set (another site).
    for cable in cables:
        for device, _ in cable:
            nodes.setdefault(device, {
                "name": device, "site": None, "role": None, "status": None,
                "in_netbox": True, "collected": device in telemetry.collected,
            })

    links = []
    cabled: set[Port] = set()
    for a, b in sorted({tuple(sorted(c)) for c in cables}):
        cabled.update((a, b))
        ends = [_end(a, b, telemetry), _end(b, a, telemetry)]
        checked = [e["check"] for e in ends if e["check"] != "unverified"]
        status = max(checked, key=_SEVERITY.__getitem__) if checked else "unverified"
        links.append({"source": "netbox", "status": status, "ends": ends})

    seen_unexpected: set[frozenset[Port]] = set()
    for port, neighbours in sorted(telemetry.lldp.items()):
        if port in cabled or port[0] not in telemetry.collected:
            continue
        for neighbour in neighbours:
            key = frozenset((port, neighbour))
            # only the fabric's switches are drawn: no servers or outside switches,
            # and not the management network (MEth to an access switch)
            if (key in seen_unexpected or neighbour[0] not in nodes
                    or port[1].startswith("MEth") or neighbour[1].startswith("MEth")):
                continue
            seen_unexpected.add(key)
            links.append(
                {
                    "source": "lldp",
                    "status": "unexpected",
                    "ends": [
                        {
                            "device": port[0],
                            "interface": port[1],
                            "oper_up": telemetry.oper_up.get(port),
                            "lldp": [{"system_name": neighbour[0], "port_id": neighbour[1]}],
                            "check": "unexpected",
                        },
                        {
                            "device": neighbour[0],
                            "interface": neighbour[1],
                            "oper_up": telemetry.oper_up.get(neighbour),
                            "lldp": [],
                            "check": "unverified",
                        },
                    ],
                }
            )

    return {"nodes": sorted(nodes.values(), key=lambda n: n["name"]), "links": links}
