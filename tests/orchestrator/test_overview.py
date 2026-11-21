"""The overview page's Prometheus data: firing alerts and the busiest ports."""

import asyncio

import httpx
from fastapi.testclient import TestClient

from orchestrator.app import app, get_netbox, get_prometheus
from orchestrator.prometheus import PrometheusClient, PrometheusError
from tests.collector.test_collector import example_netbox

NOW = 1_800_000_000.0


def fake(instant: dict[str, list[tuple[dict, str]]], ranges: dict[str, list[str]] | None = None, seen=None):
    """A Prometheus answering instant queries by exact expression; range queries by expression."""
    def handler(request: httpx.Request) -> httpx.Response:
        expr = request.url.params["query"]
        if seen is not None:
            seen.append(expr)
        if request.url.path.endswith("/query_range"):
            values = (ranges or {}).get(expr)
            result = [] if values is None else [{"metric": {}, "values": [[NOW - 60 + 30 * i, v] for i, v in enumerate(values)]}]
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": result}})
        result = [{"metric": m, "value": [NOW, v]} for m, v in instant.get(expr, [])]
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": result}})

    return PrometheusClient("http://prometheus:9090", transport=httpx.MockTransport(handler))


def rate(name: str, sel: str) -> str:
    return f"irate(fabric_interface_{name}_octets_total{{{sel}}}[30s]) * 8"


def test_firing_alerts_with_the_time_they_became_active():
    prom = fake({
        'ALERTS{alertstate="firing"}': [({"alertname": "FabricDeviceOutOfSync", "device": "leaf1",
                                          "severity": "warning", "alertstate": "firing"}, "1")],
        "ALERTS_FOR_STATE": [({"alertname": "FabricDeviceOutOfSync", "device": "leaf1"}, str(NOW - 300))],
    })
    assert asyncio.run(prom.firing_alerts()) == [
        {"alert": "FabricDeviceOutOfSync", "device": "leaf1", "severity": "warning", "since": NOW - 300}]


def test_busiest_ports_with_rates_and_history():
    sel_all = 'device=~"leaf1|east\\\\.x"'
    port = 'device="leaf1",interface="10GE1/0/3"'
    seen = []
    prom = fake({
        f"topk(5, {rate('in', sel_all)} + {rate('out', sel_all)})": [({"device": "leaf1", "interface": "10GE1/0/3"}, "3000")],
        rate("in", port): [({}, "1000")],
        rate("out", port): [({}, "2000")],
    }, {f"{rate('in', port)} + {rate('out', port)}": ["10", "20", "3000"]}, seen)

    ports = asyncio.run(prom.busiest_ports(["leaf1", "east.x"], now=NOW))

    assert ports == [{"device": "leaf1", "interface": "10GE1/0/3", "bps": 3000.0, "in_bps": 1000.0,
                      "out_bps": 2000.0, "history": [10.0, 20.0, 3000.0]}]
    assert asyncio.run(prom.busiest_ports([])) == []


def test_device_names_are_checked_before_querying():
    import pytest

    with pytest.raises(PrometheusError, match="invalid device name"):
        asyncio.run(fake({}).busiest_ports(['x"}) or vector(1']))


def test_overview_keeps_the_fabrics_alerts_and_survives_prometheus_errors():
    class Prom:
        async def firing_alerts(self):
            return [{"alert": "A", "device": "leaf1", "severity": None, "since": None},
                    {"alert": "B", "device": "east-leaf3", "severity": None, "since": None},
                    {"alert": "FabricDriftStopped", "device": None, "severity": None, "since": None}]

        async def busiest_ports(self, devices):
            assert devices == ["leaf1"]   # active devices only
            return []

        async def query(self, expr):          # control-plane series: none here
            return []

    class Broken(Prom):
        async def firing_alerts(self):
            raise PrometheusError("Prometheus query failed: 503")

    nb = example_netbox()
    app.dependency_overrides[get_netbox] = lambda: nb
    try:
        app.dependency_overrides[get_prometheus] = lambda: Prom()
        body = TestClient(app).get("/overview", params={"site": "example"}).json()
        assert [a["alert"] for a in body["alerts"]] == ["A", "FabricDriftStopped"]
        assert body["telemetry"] == {"ok": True, "error": None}

        app.dependency_overrides[get_prometheus] = lambda: Broken()
        body = TestClient(app).get("/overview", params={"site": "example"}).json()
        assert body["telemetry"]["ok"] is False and "503" in body["telemetry"]["error"]
    finally:
        app.dependency_overrides.pop(get_netbox, None)
        app.dependency_overrides.pop(get_prometheus, None)


# -- traffic on the map and per-port history -----------------------------------

def test_fabric_traffic_per_device_and_port():
    sel = 'device=~"leaf1"'
    prom = fake({
        f"irate(fabric_interface_in_octets_total{{{sel}}}[30s]) * 8": [({"device": "leaf1", "interface": "10GE1/0/3"}, "1000")],
        f"irate(fabric_interface_out_octets_total{{{sel}}}[30s]) * 8": [({"device": "leaf1", "interface": "10GE1/0/3"}, "2000")],
    })
    assert asyncio.run(prom.fabric_traffic(["leaf1"])) == {"leaf1": {"10GE1/0/3": {"in_bps": 1000.0, "out_bps": 2000.0}}}
    assert asyncio.run(prom.fabric_traffic([])) == {}


def test_interface_history_in_and_out_on_one_time_axis():
    sel = 'device="leaf1",interface="10GE1/0/3"'
    prom = fake({}, {
        f"rate(fabric_interface_in_octets_total{{{sel}}}[30s]) * 8": ["10", "20", "30"],
        f"rate(fabric_interface_out_octets_total{{{sel}}}[30s]) * 8": ["1", "2", "3"],
    })
    history = asyncio.run(prom.interface_history("leaf1", "10GE1/0/3", 60, now=NOW))
    assert history["step"] == 30
    assert [(p["in_bps"], p["out_bps"]) for p in history["points"]] == [(10.0, 1.0), (20.0, 2.0), (30.0, 3.0)]


def test_history_refuses_odd_names_and_ranges():
    import pytest

    with pytest.raises(PrometheusError, match="invalid interface name"):
        asyncio.run(fake({}).interface_history("leaf1", '10GE1/0/3"}', 60))
    app.dependency_overrides[get_prometheus] = lambda: fake({})
    try:
        client = TestClient(app)
        assert client.get("/devices/leaf1/history", params={"interface": "10GE1/0/3", "minutes": 5}).status_code == 400
        assert client.get("/devices/leaf1/history", params={"interface": 'x"}', "minutes": 60}).status_code == 400
    finally:
        app.dependency_overrides.pop(get_prometheus, None)
