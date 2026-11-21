"""From one GoFlow2 record to one stored flow row, and the context it needs.

GoFlow2 v2 prints a JSON object per flow record (`-format json`). Its field names are taken from
GoFlow2's documentation, not yet seen from a test switch: `sampler_address` (the exporting switch's MEth
address), `in_if` (the input port's ifIndex), `src_addr`, `dst_addr`, `proto` (a name such as
"TCP", or a number), `src_port`, `dst_port`, `bytes`, `packets`, `sampling_rate`,
`time_received_ns`. Unknown exporters are dropped: only switches the platform knows are stored.
"""

import ipaddress
from dataclasses import dataclass, field
from datetime import datetime, timezone

DEFAULT_RATE = 1024   # generators/netstream.py RATE: used when a record does not carry its rate
PROTOCOLS = {"ICMP": 1, "TCP": 6, "UDP": 17, "GRE": 47, "ESP": 50, "ICMPv6": 58, "SCTP": 132}


@dataclass
class Context:
    exporters: dict[str, tuple[str, str]] = field(default_factory=dict)      # MEth address -> (switch, fabric)
    ifnames: dict[tuple[str, int], str] = field(default_factory=dict)        # (switch, ifIndex) -> port
    edge: set[tuple[str, str]] = field(default_factory=set)                  # (switch, port): segments, services
    subnets: dict[str, list[tuple[ipaddress.IPv4Network, str]]] = field(default_factory=dict)   # fabric -> segments

    def segment(self, fabric: str, address: ipaddress.IPv4Address) -> str:
        """The segment whose gateway subnet holds the address ("" outside every one)."""
        return next((name for net, name in self.subnets.get(fabric, []) if address in net), "")


def _protocol(value) -> int:
    if isinstance(value, str) and not value.isdigit():
        return PROTOCOLS.get(value, 0)
    return int(value or 0)


def row(record: dict, ctx: Context) -> dict | None:
    """A row for `flows_raw`, or None for a record from an unknown exporter or not IPv4."""
    known = ctx.exporters.get(str(record.get("sampler_address") or ""))
    if known is None:
        return None
    switch, fabric = known
    try:
        src, dst = ipaddress.IPv4Address(record["src_addr"]), ipaddress.IPv4Address(record["dst_addr"])
    except (KeyError, ValueError):
        return None
    index = int(record.get("in_if") or 0)
    port = ctx.ifnames.get((switch, index), f"ifIndex {index}")
    rate = int(record.get("sampling_rate") or 0) or DEFAULT_RATE
    seconds = int(record.get("time_received_ns") or 0) / 1e9 or datetime.now(timezone.utc).timestamp()
    return {
        "time": datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "site": fabric, "exporter": switch, "interface": port,
        # Server-facing traffic is counted from edge ports; uplink (VXLAN inner) records only add what
        # those cannot see (both, totals from one).
        "kind": "edge" if (switch, port) in ctx.edge else "vxlan",
        "src": str(src), "dst": str(dst), "proto": _protocol(record.get("proto")),
        "src_port": int(record.get("src_port") or 0), "dst_port": int(record.get("dst_port") or 0),
        "bytes": int(record.get("bytes") or 0) * rate, "packets": int(record.get("packets") or 0) * rate,
        "src_segment": ctx.segment(fabric, src), "dst_segment": ctx.segment(fabric, dst),
    }
