"""Capacity trends: hourly rollups of each port and trunk from Prometheus into the database."""

import asyncio
import time
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from orchestrator import capacity, db
from orchestrator.app import app, get_netbox, get_rollups
from orchestrator.prometheus import PrometheusClient
from tests.collector.test_collector import example_netbox

HOUR = 3600
T0 = int(time.time()) // HOUR * HOUR   # this hour's start: the routes read back from the real clock


def run(coro):
    return asyncio.run(coro)


class Prom(PrometheusClient):
    """Answers range queries hour by hour: 10GE1/0/1 averages 1 Gbit/s in and peaks at 3 (p95)."""

    def __init__(self):
        super().__init__("http://prometheus")
        self.asked: list[tuple[str, float, float]] = []

    async def query(self, expr):
        assert expr.startswith("fabric_interface_bandwidth_bps{")
        return [({"device": "leaf1", "interface": "10GE1/0/1"}, 0.0, 1e10),
                ({"device": "leaf1", "interface": "Eth-Trunk1"}, 0.0, 0.0)]   # a trunk down: no speed

    async def query_range(self, expr, start, end, step):
        self.asked.append((expr, start, end))
        hours = range(int(start), int(end) + 1, int(step))
        value = (3e9 if expr.startswith("quantile") else 1e9) * (1 if "_in_" in expr else 0.5)
        if "sum by (device)" in expr:
            return [({"device": "leaf1"}, [(t, value) for t in hours])]
        return [({"device": "leaf1", "interface": "10GE1/0/1", "job": "collector"}, [(t, value) for t in hours])]


def test_queries_average_over_the_hour_and_p95_of_minute_rates():
    q = capacity._queries("in")
    assert q["avg"] == "increase(fabric_interface_in_octets_total{}[1h]) * 8 / 3600"
    assert q["p95"] == "quantile_over_time(0.95, (rate(fabric_interface_in_octets_total{}[1m]) * 8)[1h:1m])"
    trunk = capacity._queries("out", 'device="leaf1",interface=~"10GE1/0/1|10GE1/0/2"', total=True)
    assert trunk["avg"].startswith('sum by (device) (increase(fabric_interface_out_octets_total{device="leaf1",')


def test_lab_trunks_are_the_sum_of_their_members():
    nb = example_netbox()
    members = capacity.trunk_members(run(nb.find("dcim/devices")), run(nb.find("dcim/interfaces")))
    assert members[("leaf1", "Eth-Trunk1")] == ["10GE1/0/1", "10GE1/0/2"]
    assert all(device == "leaf1" for device, _ in members)   # planned switches are not counted

    rows = run(capacity.Rollups(None, nb, Prom()).read(T0, T0 + HOUR))
    port = sorted((r["hour"], r["direction"], r["avg_bps"], r["p95_bps"]) for r in rows if r["interface"] == "10GE1/0/1")
    start = datetime.fromtimestamp(T0 - HOUR, timezone.utc)   # a point at T covers the hour before it
    assert port[:2] == [(start, "in", 1e9, 3e9), (start, "out", 5e8, 1.5e9)]
    assert {r["interface"] for r in rows} >= {"10GE1/0/1", "Eth-Trunk1"}


def test_first_run_backfills_then_each_run_adds_only_new_hours(database_url):
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    prom = Prom()
    rollups = capacity.Rollups(sessions, example_netbox(), prom)
    now = T0 + 600
    first = run(rollups.run_once(now))
    assert prom.asked[0][1:] == (T0 - 15 * 86400 + HOUR, T0)          # Prometheus' 15 days
    nb = example_netbox()
    trunks = capacity.trunk_members(run(nb.find("dcim/devices")), run(nb.find("dcim/interfaces")))
    assert first == 15 * 24 * 2 * (1 + len(trunks))                     # hours x directions x (10GE1/0/1 + trunks)
    prom.asked.clear()
    assert run(rollups.run_once(now)) == 0 and prom.asked == []        # nothing new within the same hour
    assert run(rollups.run_once(now + HOUR)) > 0
    assert prom.asked[0][1:] == (T0 + HOUR, T0 + HOUR)


TODAY = date(2026, 10, 15)


def days_of(values: list[float]) -> dict[date, float]:
    """Daily 95th percentiles ending today."""
    return {TODAY - timedelta(days=len(values) - 1 - i): v for i, v in enumerate(values)}


def test_a_steady_rise_reaches_70_percent_when_the_line_says():
    # 10 Gbit/s port: 3 Gbit/s rising 0.1 Gbit/s a day reaches 7 Gbit/s in 40 days
    f = capacity.forecast(days_of([3e9 - 0.1e9 * (27 - i) for i in range(28)]), 1e10, TODAY)
    assert (f["status"], f["days"], f["earliest"], f["latest"]) == ("rising", 40, 40, 40)   # no scatter, no range
    assert f["threshold_bps"] == 7e9 and round(f["today_bps"]) == 3e9


def test_scatter_widens_the_range_and_old_days_are_ignored():
    noisy = [3e9 + (0.4e9 if i % 2 else -0.4e9) + 0.05e9 * i for i in range(28)]
    f = capacity.forecast({**days_of(noisy), TODAY - timedelta(days=60): 9e9}, 1e10, TODAY)
    assert f["status"] == "rising" and f["days_of_data"] == 28
    assert f["earliest"] < f["days"] and (f["latest"] is None or f["latest"] > f["days"])


def test_no_forecast_without_enough_days_a_speed_or_a_rise():
    assert capacity.forecast(days_of([1e9] * 6), 1e10, TODAY)["status"] == "not enough data"
    assert capacity.forecast(days_of([1e9] * 10), None, TODAY)["status"] == "no speed"
    assert capacity.forecast(days_of([2e9 - 1e7 * i for i in range(10)]), 1e10, TODAY)["status"] == "not rising"
    assert capacity.forecast(days_of([8e9] * 10), 1e10, TODAY)["days"] == 0   # already over


def test_a_day_is_the_busier_direction_s_95th_percentile_of_its_hours():
    hour = lambda h, d, v: {"hour": datetime(2026, 10, 15, h, tzinfo=timezone.utc), "direction": d, "p95_bps": v}
    rows = [hour(h, "in", float(h)) for h in range(20)] + [hour(h, "out", 5.0) for h in range(20)] + [hour(1, "in", None)]
    assert capacity.daily_p95(rows) == {TODAY: 18.0}   # nearest rank 19 of 20


def test_capacity_pages_from_the_stored_hours(database_url):
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    rollups = capacity.Rollups(sessions, example_netbox(), Prom())
    run(rollups.run_once(T0 + 600))
    now = datetime.fromtimestamp(T0, timezone.utc)
    view = run(capacity.fabric_capacity(sessions, example_netbox(), rollups.prometheus, "example", 7, now=now))
    first = view["ports"][0]
    assert (first["device"], first["interface"], first["p95_percent"]) == ("leaf1", "10GE1/0/1", 30.0)
    assert first["forecast"]["status"] == "not rising"                # flat at 3 Gbit/s
    trunk = next(p for p in view["ports"] if p["interface"] == "Eth-Trunk1")
    assert trunk["speed_bps"] is None and trunk["p95_percent"] is None

    app.dependency_overrides[get_rollups] = lambda: rollups
    app.dependency_overrides[get_netbox] = lambda: example_netbox()
    try:
        client = TestClient(app, base_url="https://testserver")
        port = client.get("/devices/leaf1/capacity", params={"interface": "10GE1/0/1", "days": 2}).json()
        # T0 is taken when the module loads; the route reads the real clock, which may have crossed
        # into the next hour by now in a long suite run (46 hours seen), dropping one more.
        assert len(port["hours"]) in (46, 47, 48) and port["hours"][0]["in_p95_bps"] == 3e9 and len(port["daily"]) >= 2
        assert client.get("/fabrics/example/capacity", params={"days": 0}).status_code == 400
    finally:
        app.dependency_overrides.pop(get_rollups, None)
        app.dependency_overrides.pop(get_netbox, None)


def test_postgres_aggregates_follow_the_python_rules(database_url):
    """the fabric page's peaks, averages and daily 95th percentiles come from Postgres; they
    must equal what the Python rules (daily_p95, nearest rank; peaks over the window) give."""
    import random

    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    rng = random.Random(7)
    now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    rows = []
    for port in ("10GE1/0/1", "10GE1/0/2"):
        for h in range(24 * 30):
            for direction in ("in", "out"):
                p95 = None if rng.random() < 0.05 else rng.uniform(0, 1e9)
                avg = None if rng.random() < 0.05 else rng.uniform(0, 5e8)
                rows.append({"device": "leaf1", "interface": port, "direction": direction,
                             "hour": now - timedelta(hours=h), "avg_bps": avg, "p95_bps": p95})

    async def store():
        async with sessions() as session:
            await session.execute(db.TrafficHour.__table__.insert(), rows)
            await session.commit()
    run(store())
    since, window = now - timedelta(days=capacity.TREND_DAYS), now - timedelta(days=7)
    found = run(capacity._aggregates(sessions, ["leaf1"], since, window))
    for port in ("10GE1/0/1", "10GE1/0/2"):
        mine = [r for r in rows if r["interface"] == port and r["hour"] >= since]
        recent = [r for r in mine if r["hour"] >= window]
        peak, avg, daily = found[("leaf1", port)]
        assert daily == capacity.daily_p95(mine)
        for d in ("in", "out"):
            assert peak[d] == max(r["p95_bps"] or 0 for r in recent if r["direction"] == d)
            values = [r["avg_bps"] for r in recent if r["direction"] == d and r["avg_bps"] is not None]
            assert abs(avg[d] - sum(values) / len(values)) < 1e-3
