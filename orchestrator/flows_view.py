"""The flows pages: top talkers, conversations and segment pairs of a fabric or one switch,
read from ClickHouse (written by the flows service). Windows up to 24 hours read the raw rows
(kept 7 days), which carry the segment names.

Edge records (server ports and service ports) are the totals; uplink records (VXLAN inner) are a
second view, because a flow between two leaves is sampled at both.
"""

WINDOWS = (5, 15, 60, 360, 1440)   # 5: the NOC's top flows
KINDS = ("edge", "vxlan")
LIMIT = 20


def _where(device: str | None) -> str:
    return ("site = {site:String} AND kind = {kind:String} AND time >= now() - INTERVAL {minutes:UInt32} MINUTE"
            + (" AND exporter = {device:String}" if device else ""))


def _numbers(rows: list[dict], seconds: int) -> list[dict]:
    """ClickHouse quotes 64-bit integers in JSON; bits/s over the window for the page."""
    for r in rows:
        for key in ("bytes", "packets", "records"):
            if key in r:
                r[key] = int(r[key])
        if "bytes" in r:
            r["bps"] = r["bytes"] * 8 / seconds
    return rows


async def view(clickhouse, site: str, minutes: int, kind: str = "edge", device: str | None = None) -> dict:
    if minutes not in WINDOWS:
        raise ValueError(f"minutes is one of {', '.join(map(str, WINDOWS))}")
    if kind not in KINDS:
        raise ValueError("kind is edge or vxlan")
    params = {"site": site, "kind": kind, "minutes": minutes, "limit": LIMIT, **({"device": device} if device else {})}
    where, seconds = _where(device), minutes * 60
    sums = "sum(bytes) AS bytes, sum(packets) AS packets, count() AS records"
    queries = {
        "talkers": f"SELECT toString(src) AS address, any(src_segment) AS segment, {sums} FROM flows_raw "
                   f"WHERE {where} GROUP BY src ORDER BY bytes DESC LIMIT {{limit:UInt32}}",
        "conversations": f"SELECT toString(src) AS src, toString(dst) AS dst, any(src_segment) AS src_segment, "
                         f"any(dst_segment) AS dst_segment, proto, dst_port, {sums} FROM flows_raw "
                         f"WHERE {where} GROUP BY src, dst, proto, dst_port ORDER BY bytes DESC LIMIT {{limit:UInt32}}",
        "segments": f"SELECT src_segment, dst_segment, {sums} FROM flows_raw WHERE {where} "
                    f"GROUP BY src_segment, dst_segment ORDER BY bytes DESC LIMIT {{limit:UInt32}}",
        "ports": f"SELECT exporter AS switch, interface, {sums} FROM flows_raw WHERE {where} "
                 f"GROUP BY exporter, interface ORDER BY bytes DESC LIMIT {{limit:UInt32}}",
        "total": f"SELECT {sums} FROM flows_raw WHERE {where}",
    }
    result = {name: _numbers(await clickhouse.rows(sql, **params), seconds) for name, sql in queries.items()}
    total = (result.pop("total") or [{"bytes": 0, "packets": 0, "records": 0, "bps": 0.0}])[0]
    return {"site": site, "device": device, "kind": kind, "minutes": minutes, "total": total, **result}
