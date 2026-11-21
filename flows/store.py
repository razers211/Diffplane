"""Flows in ClickHouse: raw rows 7 days, per minute 90 days, per hour by
segment pair 13 months. ClickHouse aggregates by itself (materialized views into SummingMergeTree
tables), so the flows service only inserts raw rows. Spoken to over its HTTP interface."""

import httpx

COLUMNS = ("site LowCardinality(String), exporter LowCardinality(String), kind LowCardinality(String)")

SCHEMA = [
    f"""CREATE TABLE IF NOT EXISTS flows_raw (
        time DateTime, {COLUMNS}, interface LowCardinality(String),
        src IPv4, dst IPv4, proto UInt8, src_port UInt16, dst_port UInt16, bytes UInt64, packets UInt64,
        src_segment LowCardinality(String), dst_segment LowCardinality(String)
    ) ENGINE = MergeTree PARTITION BY toDate(time) ORDER BY (site, time) TTL time + INTERVAL 7 DAY""",
    # By host pair, protocol and destination port (source ports are mostly ephemeral).
    f"""CREATE TABLE IF NOT EXISTS flows_minute (
        minute DateTime, {COLUMNS}, src IPv4, dst IPv4, proto UInt8, dst_port UInt16,
        bytes UInt64, packets UInt64, flows UInt64
    ) ENGINE = SummingMergeTree((bytes, packets, flows)) PARTITION BY toYYYYMM(minute)
      ORDER BY (site, minute, exporter, kind, src, dst, proto, dst_port) TTL minute + INTERVAL 90 DAY""",
    """CREATE MATERIALIZED VIEW IF NOT EXISTS flows_minute_mv TO flows_minute AS
       SELECT toStartOfMinute(time) AS minute, site, exporter, kind, src, dst, proto, dst_port,
              sum(bytes) AS bytes, sum(packets) AS packets, count() AS flows
       FROM flows_raw GROUP BY minute, site, exporter, kind, src, dst, proto, dst_port""",
    """CREATE TABLE IF NOT EXISTS flows_hour_segments (
        hour DateTime, site LowCardinality(String), kind LowCardinality(String),
        src_segment LowCardinality(String), dst_segment LowCardinality(String), bytes UInt64, packets UInt64, flows UInt64
    ) ENGINE = SummingMergeTree((bytes, packets, flows)) PARTITION BY toYYYYMM(hour)
      ORDER BY (site, hour, kind, src_segment, dst_segment) TTL hour + INTERVAL 395 DAY""",
    """CREATE MATERIALIZED VIEW IF NOT EXISTS flows_hour_segments_mv TO flows_hour_segments AS
       SELECT toStartOfHour(time) AS hour, site, kind, src_segment, dst_segment,
              sum(bytes) AS bytes, sum(packets) AS packets, count() AS flows
       FROM flows_raw GROUP BY hour, site, kind, src_segment, dst_segment""",
]


def replicated(statement: str, cluster: str) -> str:
    """The same table or view, created on every replica of the cluster with replicated engines
    (flows replicated; Keeper on the three machines)."""
    import re

    statement = re.sub(r"^(CREATE (?:TABLE|MATERIALIZED VIEW) IF NOT EXISTS \w+)", rf"\1 ON CLUSTER {cluster}", statement)
    return statement.replace("ENGINE = MergeTree", "ENGINE = ReplicatedMergeTree").replace(
        "ENGINE = SummingMergeTree(", "ENGINE = ReplicatedSummingMergeTree(")


class ClickHouse:
    def __init__(self, url: str, user: str, password: str, database: str = "flows", cluster: str | None = None):
        self._http = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=30,
                                       headers={"X-ClickHouse-User": user, "X-ClickHouse-Key": password})
        self.database = database
        self.cluster = cluster   # set on the HA servers (CLICKHOUSE_CLUSTER): tables replicated

    async def execute(self, sql: str, data: bytes | None = None) -> str:
        response = await self._http.post("/", params={"database": self.database, "query": sql}, content=data or b"")
        if response.status_code != 200:
            raise RuntimeError(f"ClickHouse: {response.text.strip()[:300]}")
        return response.text

    async def create(self) -> None:
        for statement in SCHEMA:
            await self.execute(replicated(statement, self.cluster) if self.cluster else statement)

    async def insert(self, rows: list[dict]) -> None:
        import json

        if rows:
            await self.execute("INSERT INTO flows_raw FORMAT JSONEachRow",
                               "\n".join(json.dumps(r) for r in rows).encode())

    async def rows(self, sql: str, **params) -> list[dict]:
        """A SELECT with named parameters ({name:Type} in the SQL), its rows as dicts."""
        import json

        response = await self._http.post("/", params={"database": self.database, "default_format": "JSONEachRow",
                                                      **{f"param_{k}": str(v) for k, v in params.items()}},
                                         content=sql.encode())
        if response.status_code != 200:
            raise RuntimeError(f"ClickHouse: {response.text.strip()[:300]}")
        return [json.loads(line) for line in response.text.splitlines() if line.strip()]

    async def aclose(self) -> None:
        await self._http.aclose()
