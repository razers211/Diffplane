"""Syslog messages in ClickHouse: kept 90 days, in the flows database
(replicated on the HA servers like the flows tables)."""

import json

from flows.store import ClickHouse, replicated

TABLE = """CREATE TABLE IF NOT EXISTS syslog (
    time DateTime64(3), site LowCardinality(String), device LowCardinality(String), address IPv4,
    severity UInt8, facility UInt8, tag LowCardinality(String), message String
) ENGINE = MergeTree PARTITION BY toDate(time) ORDER BY (site, device, time) TTL toDateTime(time) + INTERVAL 90 DAY"""

SEVERITIES = ("emergency", "alert", "critical", "error", "warning", "notification", "informational", "debugging")


async def create(clickhouse: ClickHouse) -> None:
    await clickhouse.execute(replicated(TABLE, clickhouse.cluster) if clickhouse.cluster else TABLE)


async def insert(clickhouse: ClickHouse, rows: list[dict]) -> None:
    if rows:
        await clickhouse.execute("INSERT INTO syslog FORMAT JSONEachRow", "\n".join(json.dumps(r) for r in rows).encode())


async def messages(clickhouse: ClickHouse, device: str | None, severity: int = 7, text: str = "", hours: int = 24,
                   limit: int = 500, site: str | None = None) -> list[dict]:
    """A switch's messages (or with `site`, a whole fabric's: the NOC), newest first: at `severity`
    or worse, containing `text` (any case)."""
    if not 0 <= severity <= 7:
        raise ValueError("severity: 0 (emergency) to 7 (debugging)")
    if not 1 <= hours <= 90 * 24:
        raise ValueError("hours: 1 to 2160 (90 days)")
    rows = await clickhouse.rows(
        "SELECT time, device, severity, facility, tag, message FROM syslog"   # JSONEachRow prints time as text
        + (" WHERE site = {site:String}" if site else " WHERE device = {device:String}") +
        " AND severity <= {severity:UInt8}"
        " AND time > now() - toIntervalHour({hours:UInt32})"
        " AND positionCaseInsensitiveUTF8(message, {text:String}) + positionCaseInsensitiveUTF8(tag, {text:String}) > 0"
        f" ORDER BY time DESC LIMIT {int(min(max(limit, 1), 2000))}",
        **({"site": site} if site else {"device": device}), severity=severity, hours=hours, text=text)
    for row in rows:
        row["severity_name"] = SEVERITIES[int(row["severity"])]
    return rows
