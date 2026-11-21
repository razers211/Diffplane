"""Traffic anomalies and flapping links: baselines by hour of day over 14 days, the three
conditions, flaps from the timeline, the metrics, and the -20 in health."""

import asyncio
from datetime import datetime, timedelta, timezone

from orchestrator import anomaly, db, health
from orchestrator.prometheus import PrometheusClient
from tests.collector.test_collector import example_netbox
from tests.orchestrator.test_health import facts

NOW = datetime(2026, 10, 15, 14, 7, tzinfo=timezone.utc)


def hours(device, interface, direction, values, hour=14):
    """One row per day at `hour`, the last day first."""
    return [{"device": device, "interface": interface, "direction": direction, "avg_bps": v,
             "hour": (NOW - timedelta(days=i + 1)).replace(hour=hour, minute=0)} for i, v in enumerate(values)]


def test_a_baseline_needs_7_days_of_that_hour():
    rows = hours("leaf1", "10GE1/0/1", "in", [100e6, 110e6, 90e6, 105e6, 95e6, 100e6, 102e6])
    rows += hours("leaf1", "10GE1/0/2", "in", [5e6] * 6)
    base = anomaly.baselines(rows)
    median, spread = base[("leaf1", "10GE1/0/1", "in", 14)]
    assert median == 100e6 and round(spread) == round(1.4826 * 5e6)
    assert ("leaf1", "10GE1/0/2", "in", 14) not in base


def test_spikes_and_drops_must_be_far_large_and_a_share_of_the_speed():
    base = (500e6, 20e6)                      # 4 spreads = 80 Mbit/s; 1 % of 10G = 100 Mbit/s
    assert anomaly.judge(700e6, base, 10e9, 10e6) == "high"
    assert anomaly.judge(50e6, base, 10e9, 10e6) == "low"
    assert anomaly.judge(570e6, base, 10e9, 10e6) is None          # within 4 spreads
    assert anomaly.judge(600e6, base, 10e9, 10e6) is None          # exactly 1 % of 10G: not more
    assert anomaly.judge(1.5e9, (1e9, 0.0), 100e9, 10e6) is None   # 500 Mbit/s is under 1 % of 100G
    assert anomaly.judge(4e6, (0.0, 0.0), 10e9, 10e6) is None      # a quiet port: under 10 Mbit/s
    assert anomaly.judge(4e6, (0.0, 0.0), 1e8, 1e6) == "high"      # 4 Mbit/s is over 1 % of 100M and the lowered minimum
    assert anomaly.judge(3e5, (5e4, 1e4), 1e8, 1e5) is None         # the example test's gentle load: under 1 % of MEth
    assert anomaly.judge(3e5, (5e4, 1e4), 1e8, 1e5, 0.0) == "high"  # ...unless the share is lowered too


def test_three_downs_in_30_minutes_is_flapping():
    downs = [("leaf1", "10GE1/0/3", NOW - timedelta(minutes=m)) for m in (2, 11, 29)]
    downs += [("leaf1", "10GE1/0/4", NOW - timedelta(minutes=m)) for m in (2, 11, 31)]
    assert anomaly.flapping(downs, NOW) == {("leaf1", "10GE1/0/3"): 3}


def test_a_flapping_port_costs_20_once_even_while_down():
    in_use = {"leaf1": {"10GE1/0/3", "10GE1/0/4"}}
    up = facts(in_use=in_use, oper={("leaf1", "10GE1/0/3"): True, ("leaf1", "10GE1/0/4"): False},
               flapping={("leaf1", "10GE1/0/3"): 3, ("leaf1", "10GE1/0/4"): 4})
    h = health.switch_health("leaf1", up)
    assert [(r["points"], r["text"]) for r in h["reasons"]] == [
        (-20, "10GE1/0/3 is flapping (3 downs in 30 min)"),
        (-20, "10GE1/0/4 is down (flapping: 4 downs in 30 min)")]


class Prom(PrometheusClient):
    """leaf1's MEth0/0/0 at 40 Mbit/s in, Eth-Trunk1's members 10GE1/0/1 and 10GE1/0/2 at 30 each."""

    def __init__(self):
        super().__init__("http://prometheus")

    async def query(self, expr):
        if expr == "fabric_interface_bandwidth_bps":
            return [({"device": "leaf1", "interface": "MEth0/0/0"}, 0.0, 1e8),
                    ({"device": "leaf1", "interface": "Eth-Trunk1"}, 0.0, 2e10)]
        if expr.startswith("rate(fabric_interface_in_octets_total"):
            return [({"device": "leaf1", "interface": i}, 0.0, v)
                    for i, v in (("MEth0/0/0", 40e6), ("10GE1/0/1", 30e6), ("10GE1/0/2", 30e6))]
        return []


def test_the_lab_meth_and_a_trunk_against_their_stored_hours(database_url):
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))

    async def store():
        async with sessions() as session:
            for r in hours("leaf1", "MEth0/0/0", "in", [2e6] * 10) + hours("leaf1", "Eth-Trunk1", "in", [60e6] * 10):
                session.add(db.TrafficHour(device=r["device"], interface=r["interface"], direction=r["direction"],
                                           hour=r["hour"], avg_bps=r["avg_bps"], p95_bps=r["avg_bps"]))
            for m in (1, 9, 20):
                session.add(db.Event(at=NOW - timedelta(minutes=m), site="example", device="leaf1", kind="link",
                                     subject="Eth-Trunk1", state="down", previous="up", severity="problem"))
            await session.commit()
    asyncio.run(store())

    reader = anomaly.Anomalies(sessions, example_netbox(), Prom())
    found = asyncio.run(reader.run_once(NOW))
    assert [(a["interface"], a["direction"], a["kind"]) for a in found] == [("MEth0/0/0", "in", "high")]   # trunk: as usual
    text = reader.metrics().decode()
    assert 'fabric_traffic_anomaly{device="leaf1",direction="in",interface="MEth0/0/0",kind="high"} 4e+07' in text
    assert 'fabric_link_flapping{device="leaf1",interface="Eth-Trunk1"} 3.0' in text


def test_the_one_shot_check_says_now_usual_and_verdict_per_port():
    reader = anomaly.Anomalies(None, None, None)
    reader._baselines = {("leaf1", "MEth0/0/0", "in", 14): (2e5, 1e4)}
    rates = {("leaf1", "MEth0/0/0", "in"): 2.1e5, ("leaf1", "MEth0/0/0", "out"): 3e5, ("leaf9", "MEth0/0/0", "in"): 1.0,
             ("leaf1", "10GE1/0/9", "in"): 0.0}
    lines = anomaly.report(reader, "leaf1", rates, {("leaf1", "MEth0/0/0"): 1e8}, NOW)
    assert lines == ["MEth0/0/0    in   now 0.210 Mbit/s  usual 0.200 Mbit/s ± 0.010 Mbit/s  as usual",
                     "MEth0/0/0    out  now 0.300 Mbit/s  no baseline (needs 7 days of this hour)",
                     "(1 idle port directions left out: no traffic now or usually)"]
