"""Health scores: fixed deductions per occurrence, planned partners never counted, the
fabric's score its worst switch's or link's."""

import asyncio
import time

from orchestrator import health
from orchestrator.prometheus import PrometheusClient
from tests.collector.test_collector import example_netbox
from tests.orchestrator.test_hardware import example_series


def device(name: str, status: str = "active") -> dict:
    return {"id": hash(name) % 1000, "name": name, "status": {"value": status}}


def facts(**kw) -> health.Facts:
    f = health.Facts(devices={n: device(n) for n in ("leaf1", "leaf2", "spine1")}, collected={"leaf1", "leaf2", "spine1"})
    f.devices["leaf3"] = device("leaf3", "planned")
    for key, value in kw.items():
        setattr(f, key, value)
    return f


def test_a_healthy_switch_scores_100():
    h = health.switch_health("leaf1", facts())
    assert (h["score"], h["level"], h["reasons"]) == (100, "good", [])


def test_deductions_add_up_per_occurrence_and_stop_at_0():
    f = facts(in_use={"leaf1": {"10GE1/0/1", "10GE1/0/2", "Eth-Trunk1"}},
              oper={("leaf1", "10GE1/0/1"): False, ("leaf1", "10GE1/0/2"): False, ("leaf1", "Eth-Trunk1"): True},
              errors={("leaf1", "Eth-Trunk1")}, drift={"leaf1": "out_of_sync"})
    h = health.switch_health("leaf1", f)
    assert h["score"] == 100 - 20 - 20 - 10 - 10 and h["level"] == "critical"   # under 60
    assert [r["points"] for r in h["reasons"]] == [-20, -20, -10, -10]
    f.collected = set()
    f.hardware = {"leaf1": {"power": [{"module": "PWR:1/1", "ok": False, "status": "abnormal"}]}}
    assert health.switch_health("leaf1", f)["score"] == 0          # never below 0
    assert health.switch_health("leaf1", f)["level"] == "critical"


def test_a_port_in_use_that_protection_acts_on_costs_10():
    """a trip on a port in use costs -10 with its words; on an unused port nothing."""
    f = facts(in_use={"leaf1": {"10GE1/0/10"}},
              tripped={("leaf1", "10GE1/0/10"): [{"cause": "loopback-detect", "state": "error-down", "detail": "", "reopen": 31}],
                       ("leaf1", "10GE1/0/20"): [{"cause": "storm-control", "state": "block", "detail": "broadcast", "reopen": None}]})
    h = health.switch_health("leaf1", f)
    assert h["score"] == 90
    assert h["reasons"] == [{"points": -10, "text": "10GE1/0/10: loop detection, shut, reopens in 31 s"}]


def test_planned_partners_and_expected_down_ports_never_count():
    plane = {"bgp": [{"peer": "10.10.0.1", "switch": "spine1", "family": "evpn", "state": "idle", "up": False, "expected_down": True}],
             "ospf": [], "mlag": {"partner": "leaf3", "heartbeat": False, "expected_down": True, "peer_links": {"Eth-Trunk0": False}}}
    f = facts(in_use={"leaf1": {"100GE1/0/1"}}, oper={("leaf1", "100GE1/0/1"): False},
              expected_down={("leaf1", "100GE1/0/1")}, control={"leaf1": plane})
    assert health.switch_health("leaf1", f)["reasons"] == []


def test_control_plane_and_hardware_rules():
    plane = {"bgp": [{"peer": "10.10.0.1", "switch": "spine1", "family": "evpn", "state": "active", "up": False, "expected_down": False}],
             "ospf": [{"neighbor": "10.10.0.1", "interface": "100GE1/0/1", "state": "init", "up": False}],
             "mlag": {"partner": "leaf2", "heartbeat": False, "expected_down": False, "peer_links": {"Eth-Trunk0": False}}}
    hw = {"power": [], "fans": [{"fan": "FAN:1/2", "present": False}],
          "sensors": [{"slot": "1", "sensor": "NP1", "celsius": 109.0, "normal": True, "thresholds": {"minor": 103, "major": 108}},
                      {"slot": "1", "sensor": "CPU", "celsius": 104.0, "normal": True, "thresholds": {"minor": 103, "major": 108}}],
          "cpu": [{"slot": "1", "usage": 95.0, "ok": False}], "memory": [],
          "optics": [{"port": "10GE1/0/5", "level": "alarm", "reasons": ["received -40 dBm past the module's alarm threshold"]},
                     {"port": "10GE1/0/6", "level": "unused", "reasons": []}]}
    reasons = {r["text"]: r["points"] for r in health.switch_health("leaf1", facts(control={"leaf1": plane}, hardware={"leaf1": hw}))["reasons"]}
    assert reasons == {
        "M-LAG heartbeat with leaf2 lost": -40, "M-LAG peer-link Eth-Trunk0 down": -40,
        "BGP peer 10.10.0.1 (spine1), evpn: active": -20, "OSPF neighbour 10.10.0.1 on 100GE1/0/1: init": -20,
        "fan FAN:1/2 missing": -20, "temperature NP1 (slot 1) at 109 °C, at its Major threshold": -20,
        "optic in 10GE1/0/5: received -40 dBm past the module's alarm threshold": -20,
        "temperature CPU (slot 1) at 104 °C, at its Minor threshold": -10, "CPU of slot 1 at 95 %": -10,
    }


def link(a: tuple, b: tuple, check="ok", up=(True, True)) -> dict:
    return {"source": "netbox", "status": check, "ends": [
        {"device": a[0], "interface": a[1], "oper_up": up[0], "check": check, "lldp": []},
        {"device": b[0], "interface": b[1], "oper_up": up[1], "check": "ok", "lldp": []}]}


def test_links_between_active_switches_only():
    f = facts(errors={("spine1", "100GE1/0/2")})
    down = health.link_health(link(("leaf1", "100GE1/0/1"), ("spine1", "100GE1/0/1"), "missing", (False, True)), f)
    assert down["score"] == 80 and [r["text"] for r in down["reasons"]] == ["leaf1 100GE1/0/1 is down"]   # no LLDP: it is down
    wrong = health.link_health(link(("leaf2", "100GE1/0/1"), ("spine1", "100GE1/0/2"), "mismatch"), f)
    assert wrong["score"] == 80 and wrong["name"] == "leaf2 100GE1/0/1 – spine1 100GE1/0/2"
    assert health.link_health(link(("leaf3", "100GE1/0/1"), ("spine1", "100GE1/0/3"), "missing", (None, False)), f) is None


def test_segments_from_their_ports_and_service():
    segment = {"name": "web", "attachments": [{"device": "leaf1", "interface": "10GE1/0/3"}, {"device": "leaf3", "interface": "10GE1/0/3"}],
               "routing": {"firewall": "FW"}}
    services = [{"name": "FW", "device": "leaf2", "devices": ["leaf2"], "port": "10GE1/0/4"}]
    f = facts(oper={("leaf1", "10GE1/0/3"): False, ("leaf2", "10GE1/0/4"): False}, services=services)
    h = health.segment_health(segment, f)
    assert [r["text"] for r in h["reasons"]] == ["leaf1 10GE1/0/3 is down", "service FW: leaf2 10GE1/0/4 is down"]   # leaf3 planned


def test_the_fabric_takes_its_worst():
    f = facts(in_use={"leaf2": {"10GE1/0/1"}}, oper={("leaf2", "10GE1/0/1"): False}, drift={"spine1": "out_of_sync"})
    view = health.fabric_health("east", f)
    assert (view["score"], view["level"], view["worst"]) == (80, "warning", {"kind": "switch", "name": "leaf2"})
    assert view["others_below_good"] == 0 and view["planned"] == ["leaf3"]            # spine1 at 90 is still good
    assert [s["name"] for s in view["switches"]] == ["leaf1", "leaf2", "spine1"]


class Prom(PrometheusClient):
    """Answers the queries `facts` sends from fixed series: leaf1 polled now, its example hardware."""

    def __init__(self, oper: dict[str, bool]):
        super().__init__("http://prometheus")
        self.oper = oper

    async def query(self, expr):
        now = time.time()
        if expr == "fabric_collector_device_up":
            return [({"device": "leaf1"}, now, 1.0)]
        if expr == "fabric_collector_last_success_timestamp_seconds":
            return [({"device": "leaf1"}, now, now)]
        if expr == "fabric_interface_oper_up":
            return [({"device": "leaf1", "interface": i}, now, 1.0 if up else 0.0) for i, up in self.oper.items()]
        if "optic_" in expr:
            return example_series()
        return []


def test_leaf1_from_netbox_and_prometheus():
    """The example as it is: partners planned (uplink, peer-link expected down), PWR:1/1 not fed, Eth-Trunk1 without servers."""
    oper = {"100GE1/0/1": False, "100GE1/0/5": False, "Eth-Trunk0": False, "10GE1/0/1": False, "Eth-Trunk1": False}
    f = asyncio.run(health.facts(example_netbox(), Prom(oper), "example", [], 120))
    view = health.fabric_health("example", f)
    leaf = view["switches"][0]
    assert leaf["name"] == "leaf1" and [r["text"] for r in leaf["reasons"]] == [
        "power module PWR:1/1 is abnormal", "Eth-Trunk1 is down (members 10GE1/0/1)"]
    assert view["score"] == leaf["score"] == 40 and view["links"] == [] and set(view["planned"]) == {"leaf2", "spine1"}


def test_a_down_trunk_counts_once_a_lost_member_of_an_up_trunk_on_its_own():
    """the trunk -20 once, its down members named in that reason."""
    trunk_of = {("leaf1", p): "Eth-Trunk1" for p in ("10GE1/0/1", "10GE1/0/2")}
    in_use = {"leaf1": {"10GE1/0/1", "10GE1/0/2", "Eth-Trunk1"}}
    down = {("leaf1", "10GE1/0/1"): False, ("leaf1", "10GE1/0/2"): False, ("leaf1", "Eth-Trunk1"): False}
    h = health.switch_health("leaf1", facts(in_use=in_use, oper=down, trunk_of=trunk_of))
    assert [(r["points"], r["text"]) for r in h["reasons"]] == [(-20, "Eth-Trunk1 is down (members 10GE1/0/1, 10GE1/0/2)")]
    half = {**down, ("leaf1", "10GE1/0/2"): True, ("leaf1", "Eth-Trunk1"): True}
    h = health.switch_health("leaf1", facts(in_use=in_use, oper=half, trunk_of=trunk_of))
    assert [(r["points"], r["text"]) for r in h["reasons"]] == [(-20, "10GE1/0/1 is down")]
