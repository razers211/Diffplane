import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from orchestrator.app import app, get_netbox, get_prometheus
from orchestrator.prometheus import PrometheusClient, PrometheusError
from orchestrator.topology import Device, Telemetry, build
from tests.helpers import seed
from seed.model import load
from tests.seed.fake_netbox import FakeNetBox

LEAF, SPINE = ("leaf1", "100GE1/0/1"), ("spine1", "100GE1/0/1")
DEVICES = [Device("leaf1", "example", "leaf", "active"), Device("spine1", "example", "spine", "active")]


def only_link(result: dict) -> dict:
    (link,) = result["links"]
    return link


def test_ok_when_both_ends_see_each_other():
    t = Telemetry(lldp={LEAF: [SPINE], SPINE: [LEAF]}, collected={"leaf1", "spine1"})

    link = only_link(build(DEVICES, [(LEAF, SPINE)], t))

    assert link["status"] == "ok"
    assert [e["check"] for e in link["ends"]] == ["ok", "ok"]


def test_missing_when_no_neighbour_is_seen():
    t = Telemetry(collected={"leaf1"})

    link = only_link(build(DEVICES, [(LEAF, SPINE)], t))

    assert link["status"] == "missing"
    checks = {e["device"]: e["check"] for e in link["ends"]}
    assert checks == {"leaf1": "missing", "spine1": "unverified"}


def test_mismatch_when_a_different_neighbour_is_seen():
    t = Telemetry(lldp={LEAF: [("spine2", "100GE1/0/1")]}, collected={"leaf1"})

    link = only_link(build(DEVICES, [(LEAF, SPINE)], t))

    assert link["status"] == "mismatch"
    leaf_end = next(e for e in link["ends"] if e["device"] == "leaf1")
    assert leaf_end["lldp"] == [{"system_name": "spine2", "port_id": "100GE1/0/1"}]


def test_wrong_port_on_the_right_device_is_a_mismatch():
    t = Telemetry(lldp={LEAF: [("spine1", "100GE1/0/2")]}, collected={"leaf1"})

    assert only_link(build(DEVICES, [(LEAF, SPINE)], t))["status"] == "mismatch"


def test_worst_end_wins():
    t = Telemetry(lldp={SPINE: [LEAF], LEAF: [("x", "y")]}, collected={"leaf1", "spine1"})

    assert only_link(build(DEVICES, [(LEAF, SPINE)], t))["status"] == "mismatch"


def test_unverified_without_fresh_data():
    assert only_link(build(DEVICES, [(LEAF, SPINE)], Telemetry()))["status"] == "unverified"


def test_cable_listed_from_both_ends_is_one_link():
    t = Telemetry(collected={"leaf1"})

    assert len(build(DEVICES, [(LEAF, SPINE), (SPINE, LEAF)], t)["links"]) == 1


def test_lldp_between_switches_without_cable_is_unexpected():
    t = Telemetry(lldp={("leaf1", "10GE1/0/7"): [("spine1", "10GE1/0/9")]}, collected={"leaf1"})

    (link,) = build(DEVICES, [], t)["links"]

    assert link["status"] == "unexpected" and link["source"] == "lldp"


def test_lldp_neighbours_outside_netbox_are_not_drawn():
    t = Telemetry(lldp={("leaf1", "10GE1/0/7"): [("server9", "eth0")]}, collected={"leaf1"})

    result = build(DEVICES, [], t)

    assert result["links"] == [] and {n["name"] for n in result["nodes"]} == {"leaf1", "spine1"}


def test_lldp_over_the_management_network_is_left_out():
    # imported fabrics: every switch's MEth0/0/0 sees an access port of sw-oob1, and sw-oob1 sees the MEths
    t = Telemetry(lldp={("leaf1", "MEth0/0/0"): [("spine1", "10GE1/0/40")],
                        ("spine1", "10GE1/0/40"): [("leaf1", "MEth0/0/0")]}, collected={"leaf1", "spine1"})

    assert build(DEVICES, [], t)["links"] == []


def test_oper_state_is_attached_to_ends():
    t = Telemetry(oper_up={LEAF: True, SPINE: False}, collected={"leaf1"})

    ends = {e["device"]: e["oper_up"] for e in only_link(build(DEVICES, [(LEAF, SPINE)], t))["ends"]}

    assert ends == {"leaf1": True, "spine1": False}


# -- Prometheus client -------------------------------------------------------------

def prometheus_with(series: dict[str, list[tuple[dict, float, str]]]) -> PrometheusClient:
    def handler(request: httpx.Request) -> httpx.Response:
        result = [
            {"metric": labels, "value": [ts, value]}
            for labels, ts, value in series.get(request.url.params["query"], [])
        ]
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": result}})

    return PrometheusClient("http://prometheus:9090", transport=httpx.MockTransport(handler))


NOW = 1_800_000_000.0


def test_telemetry_from_prometheus():
    prom = prometheus_with({
        "fabric_collector_device_up": [({"device": "leaf1"}, NOW, "1"), ({"device": "stale"}, NOW, "1"),
                                       ({"device": "down"}, NOW, "0")],
        "fabric_collector_last_success_timestamp_seconds": [
            ({"device": "leaf1"}, NOW, str(NOW - 30)), ({"device": "stale"}, NOW, str(NOW - 600)),
            ({"device": "down"}, NOW, str(NOW - 30))],
        "fabric_interface_oper_up": [({"device": "leaf1", "interface": "MEth0/0/0"}, NOW, "1")],
        "fabric_lldp_neighbor_info": [({"device": "leaf1", "interface": "100GE1/0/2",
                                        "neighbor_system_name": "spine1", "neighbor_port_id": "100GE1/0/9",
                                        "neighbor_chassis_id": "x"}, NOW, "1")],
    })

    t = asyncio.run(prom.telemetry(max_age=120))

    assert t.collected == {"leaf1"}
    assert t.oper_up == {("leaf1", "MEth0/0/0"): True}
    assert t.lldp == {("leaf1", "100GE1/0/2"): [("spine1", "100GE1/0/9")]}


def test_prometheus_errors_are_reported():
    prom = PrometheusClient("http://prometheus:9090", transport=httpx.MockTransport(lambda r: httpx.Response(503)))

    with pytest.raises(PrometheusError):
        asyncio.run(prom.telemetry(max_age=120))


# -- endpoints, on the seeded example fabric -------------------------------------------

class FakePrometheus:
    def __init__(self, telemetry: Telemetry | Exception):
        self._telemetry = telemetry

    async def telemetry(self, max_age):
        if isinstance(self._telemetry, Exception):
            raise self._telemetry
        return self._telemetry


@pytest.fixture
def example() -> FakeNetBox:
    nb = FakeNetBox()
    asyncio.run(seed(nb, load(Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "example-small.yaml")))
    return nb


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def client(netbox, prometheus) -> TestClient:
    app.dependency_overrides[get_netbox] = lambda: netbox
    app.dependency_overrides[get_prometheus] = lambda: prometheus
    return TestClient(app)


def statuses(body: dict) -> dict[tuple[str, ...], str]:
    return {
        tuple(sorted(f"{e['device']} {e['interface']}" for e in link["ends"])): link["status"]
        for link in body["links"]
    }


def test_lab_topology_flags_cables_lldp_does_not_see(example):
    # The real bench: leaf1 is polled and sees no neighbours on its cabled ports.
    body = client(example, FakePrometheus(Telemetry(collected={"leaf1"}))).get("/topology?site=example").json()

    assert body["telemetry"] == {"ok": True, "error": None}
    assert statuses(body) == {
        ("leaf1 100GE1/0/1", "spine1 100GE1/0/1"): "missing",
        ("leaf1 100GE1/0/5", "leaf2 100GE1/0/5"): "missing",
        ("leaf1 100GE1/0/6", "leaf2 100GE1/0/6"): "missing",
        ("leaf2 100GE1/0/1", "spine1 100GE1/0/2"): "unverified",
    }
    nodes = {n["name"]: (n["status"], n["role"], n["collected"]) for n in body["nodes"]}
    assert nodes == {
        "leaf1": ("active", "leaf", True),
        "leaf2": ("planned", "leaf", False),
        "spine1": ("planned", "spine", False),
    }


def test_topology_without_prometheus_is_unverified(example):
    body = client(example, FakePrometheus(PrometheusError("down"))).get("/topology?site=example").json()

    assert body["telemetry"] == {"ok": False, "error": "down"}
    assert set(statuses(body).values()) == {"unverified"}


def test_topology_with_prometheus_not_configured(example):
    body = client(example, None).get("/topology").json()

    assert body["telemetry"]["ok"] is False
    assert len(body["links"]) == 4


def test_devices_list(example):
    body = client(example, None).get("/devices?site=example").json()

    assert body == [
        {"name": "leaf1", "site": "example", "role": "leaf", "status": "active", "primary_ip": "172.24.0.11/24", "location": None, "rack": None, "position": None, "face": None, "pinned": False, "meth_mac": None, "peer": "leaf2"},
        {"name": "leaf2", "site": "example", "role": "leaf", "status": "planned", "primary_ip": None, "location": None, "rack": None, "position": None, "face": None, "pinned": False, "meth_mac": None, "peer": "leaf1"},
        {"name": "spine1", "site": "example", "role": "spine", "status": "planned", "primary_ip": None, "location": None, "rack": None, "position": None, "face": None, "pinned": False, "meth_mac": None, "peer": None},
    ]


# -- interface traffic ---------------------------------------------------------

def traffic_series() -> dict:
    leaf = {"device": "leaf1"}
    series = {
        'fabric_interface_oper_up{device="leaf1"}': [({**leaf, "interface": "10GE1/0/10"}, NOW, "1"),
                                                         ({**leaf, "interface": "10GE1/0/2"}, NOW, "0")],
        'irate(fabric_interface_in_octets_total{device="leaf1"}[30s]) * 8': [
            ({**leaf, "interface": "10GE1/0/10"}, NOW, "2500000000")],
        'irate(fabric_interface_out_octets_total{device="leaf1"}[30s]) * 8': [
            ({**leaf, "interface": "10GE1/0/10"}, NOW, "1200000")],
        'increase(fabric_interface_in_errors_total{device="leaf1"}[5m])': [
            ({**leaf, "interface": "10GE1/0/10"}, NOW, "3")],
    }
    for name in ("fabric_collector_last_success_timestamp_seconds", "fabric_collector_device_up"):
        series[f'{name}{{device="leaf1"}}'] = [(leaf, NOW, "1")]
    return series


def test_interface_traffic_from_prometheus():
    traffic = asyncio.run(prometheus_with(traffic_series()).interface_traffic("leaf1"))

    assert traffic == {"10GE1/0/10": {"in_bps": 2.5e9, "out_bps": 1.2e6, "in_errors": 3.0}}


def test_interface_traffic_rejects_label_injection():
    with pytest.raises(PrometheusError):
        asyncio.run(prometheus_with({}).interface_traffic('x"} or vector(1) or {a="'))


def test_device_interfaces_endpoint(example):
    body = client(example, prometheus_with(traffic_series())).get("/devices/leaf1/interfaces").json()

    assert [i["interface"] for i in body["interfaces"]] == ["10GE1/0/2", "10GE1/0/10"]   # natural order
    assert body["interfaces"][1] == {"interface": "10GE1/0/10", "oper_up": True, "in_bps": 2.5e9,
                                     "out_bps": 1.2e6, "in_errors": 3.0}


def test_device_interfaces_needs_prometheus(example):
    assert client(example, None).get("/devices/leaf1/interfaces").status_code == 503
