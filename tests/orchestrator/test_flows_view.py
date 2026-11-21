"""The flows pages: what is asked of ClickHouse, and the API's answers."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from orchestrator import flows_view
from orchestrator.app import app, get_clickhouse, get_netbox
from tests.collector.test_collector import example_netbox


class ClickHouse:
    """Answers like ClickHouse's JSONEachRow: 64-bit integers quoted."""

    def __init__(self):
        self.asked: list[tuple[str, dict]] = []

    async def rows(self, sql, **params):
        self.asked.append((sql, params))
        if "GROUP BY src ORDER" in sql:
            return [{"address": "10.100.0.10", "segment": "web", "bytes": "75000000", "packets": "50000", "records": "40"}]
        if sql.startswith("SELECT sum("):
            return [{"bytes": "75000000", "packets": "50000", "records": "40"}]
        return []


def test_a_fabric_view_counts_edge_ports_by_default_and_gives_rates():
    ch = ClickHouse()
    v = asyncio.run(flows_view.view(ch, "example", 60))
    assert v["talkers"] == [{"address": "10.100.0.10", "segment": "web", "bytes": 75_000_000, "packets": 50_000,
                             "records": 40, "bps": 75_000_000 * 8 / 3600}]
    assert v["total"]["bytes"] == 75_000_000 and v["conversations"] == []
    assert all(p == {"site": "example", "kind": "edge", "minutes": 60, "limit": 20} for _, p in ch.asked)
    assert all("{site:String}" in sql and "{device:String}" not in sql for sql, _ in ch.asked)   # the whole fabric


def test_a_switch_view_adds_its_name_as_a_parameter_never_into_the_sql():
    ch = ClickHouse()
    asyncio.run(flows_view.view(ch, "example", 15, "vxlan", "leaf1"))
    assert all(p["device"] == "leaf1" and p["kind"] == "vxlan" for _, p in ch.asked)
    assert all("leaf1" not in sql and "{device:String}" in sql for sql, _ in ch.asked)


@pytest.mark.parametrize("minutes, kind", [(30, "edge"), (60, "all")])
def test_windows_and_kinds_are_checked(minutes, kind):
    with pytest.raises(ValueError):
        asyncio.run(flows_view.view(ClickHouse(), "example", minutes, kind))


def test_the_api():
    client = TestClient(app, base_url="https://testserver")
    assert client.get("/fabrics/example/flows").status_code == 503          # no flows store configured
    app.dependency_overrides[get_clickhouse] = lambda: ClickHouse()
    app.dependency_overrides[get_netbox] = lambda: example_netbox()
    try:
        assert client.get("/fabrics/example/flows", params={"minutes": 1440}).json()["total"]["records"] == 40
        assert client.get("/fabrics/example/flows", params={"minutes": 7}).status_code == 400
        assert client.get("/fabrics/example/flows", params={"minutes": 5}).status_code == 200   # the NOC's window
        assert client.get("/devices/leaf1/flows").json()["device"] == "leaf1"
        assert client.get("/devices/nope/flows").status_code == 404
    finally:
        app.dependency_overrides.pop(get_clickhouse, None)
        app.dependency_overrides.pop(get_netbox, None)
