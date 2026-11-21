"""A switch's hardware for the GUI: the collector's series of leaf1's recorded replies, shaped,
with an optic's level a problem only on a port in use."""

import asyncio

from prometheus_client.parser import text_string_to_metric_families

from collector import hardware as collected
from collector.metrics import FabricCollector
from orchestrator import faceplate as fp
from orchestrator.hardware import shape
from orchestrator.segments import Segments
from tests.collector.test_collector import exposition, example_netbox
from tests.collector.test_hardware import EXAMPLE


def example_series(edit=lambda text: text) -> list[tuple[dict, float, float]]:
    """leaf1's hardware as Prometheus would return it: (labels with __name__, time, value)."""
    metrics = FabricCollector()
    metrics.record_hardware("leaf1", "environment", collected.parse(
        {"fans-power": EXAMPLE["fans-power"], "cpu-memory": EXAMPLE["cpu-memory"], "temperature": edit(EXAMPLE["temperature"])}))
    metrics.record_hardware("leaf1", "optics", collected.parse({"optics": EXAMPLE["optics"]}))
    return [({"__name__": s.name, **s.labels}, 0.0, s.value)
            for family in text_string_to_metric_families(exposition(metrics)) for s in family.samples]


def test_leaf1_hardware_shaped_with_unused_optics_not_problems():
    view = shape("leaf1", example_series(), in_use=set())
    assert len(view["optics"]) == 19 and view["optics"][0]["port"] == "10GE1/0/5"     # 10GE1/0/5 before 10GE1/0/10
    assert {o["level"] for o in view["optics"]} == {"unused"}
    assert view["problems"] == ["power module PWR:1/1 is abnormal"]                    # not fed on purpose
    assert view["power"][1] == {"module": "PWR:1/2", "ok": True, "status": "normal", "input": 157, "output": 143}
    assert view["fans"][0] == {"fan": "FAN:1/1", "present": True, "speed": 40, "rpm": 9900}
    assert view["cpu"] == [{"slot": "1", "usage": 20, "threshold": 90, "ok": True}]
    np1 = next(s for s in view["sensors"] if s["sensor"] == "NP1")
    assert np1 == {"slot": "1", "sensor": "NP1", "celsius": 46, "normal": True,
                   "thresholds": {"minor": 103, "major": 108, "fatal": 115}, "ok": True}
    assert view["collected"] and view["refused"] == []


def test_an_optic_without_light_on_a_port_in_use_is_an_alarm():
    view = shape("leaf1", example_series(), in_use={"10GE1/0/5"})
    optic = view["optics"][0]
    assert optic["level"] == "alarm" and optic["channels"] == [{"channel": "0", "rx_power": -40, "tx_power": -2.04, "bias": 5.84}]
    assert optic["thresholds"]["rx_power"]["low_alarm"] == -11.9
    assert "optic in 10GE1/0/5: received -40 dBm past the module's alarm threshold" in view["problems"]


def test_a_hot_sensor_is_a_problem():
    hot = lambda text: text.replace("Normal         70     75     85       57       60    28",
                                    "Normal         70     75     85       57       60    76")
    view = shape("leaf1", example_series(hot), in_use=set())
    assert "temperature TEMP_A in slot 1: 76 °C, at its major threshold" in view["problems"]


def test_ports_in_use_for_the_metrics():
    nb = example_netbox()
    asyncio.run(Segments(nb).create("example", "web", 10500, 500))
    asyncio.run(Segments(nb).attach("example", "leaf1", "10GE1/0/3", 10500, None))
    found = asyncio.run(fp.ports_in_use(nb, ["leaf1"]))
    assert {"10GE1/0/3", "100GE1/0/1", "10GE1/0/1"} <= found["leaf1"] and "10GE1/0/12" not in found["leaf1"]
    text = fp.in_use_metrics(found).decode()
    assert 'fabric_port_in_use{device="leaf1",interface="10GE1/0/3"} 1.0' in text


def test_planned_partners_for_the_alert_rules():
    """the addresses of planned switches and the switches whose M-LAG peer is planned."""
    from orchestrator import controlplane

    text = asyncio.run(controlplane.planned_metrics(example_netbox())).decode()
    assert 'fabric_mlag_peer_planned{device="leaf1"} 1.0' in text
    planned = [line for line in text.splitlines() if line.startswith("fabric_planned_address{")]
    assert planned and 'fabric_planned_address{peer="10.10.0.1"} 1.0' in text   # spine1's loopback
    assert 'device="leaf2"' not in text                                      # planned itself: not polled
