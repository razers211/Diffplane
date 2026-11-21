"""Capacity trends: hourly traffic per port and trunk, kept 13 months.

Every hour the orchestrator asks Prometheus, for each complete hour not yet stored, each port's and
Eth-Trunk's average and 95th percentile in bits/s per direction, and writes them into the platform
database (table `traffic_hourly`, `CAPACITY_KEEP_DAYS`, 395). The first run fills in what Prometheus
still holds (its 15 days). The average is the counter's increase over the hour; the 95th percentile
is taken over the hour's per-minute rates. A trunk is the sum of its member ports (the collector
counts physical ports only), its members as NetBox has them now.
"""

import asyncio
import logging
import math
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from orchestrator.faceplate import FRONT_PANEL
from orchestrator.db import TrafficHour
from orchestrator.routing_intent import status_of

log = logging.getLogger("orchestrator.capacity")

HOUR = 3600
DIRECTIONS = ("in", "out")


def _queries(direction: str, selector: str = "", total: bool = False) -> dict[str, str]:
    """PromQL for one hour ending at each evaluation time: avg and p95 in bits/s.
    total: summed per device (a trunk's members)."""
    series = f"fabric_interface_{direction}_octets_total{{{selector}}}"
    wrap = (lambda e: f"sum by (device) ({e})") if total else (lambda e: e)
    return {
        "avg": f"{wrap(f'increase({series}[1h])')} * 8 / {HOUR}",
        "p95": f"quantile_over_time(0.95, ({wrap(f'rate({series}[1m])')} * 8)[1h:1m])",
    }


def trunk_members(devices: list[dict], interfaces: list[dict]) -> dict[tuple[str, str], list[str]]:
    """(active switch, Eth-Trunk) -> its member ports, from NetBox."""
    active = {d["id"]: d["name"] for d in devices if status_of(d) == "active"}
    members: dict[tuple[str, str], list[str]] = defaultdict(list)
    for i in interfaces:
        device = active.get((i.get("device") or {}).get("id"))
        lag = i.get("lag")
        if device and isinstance(lag, dict) and lag.get("name") and FRONT_PANEL.fullmatch(i["name"]):
            members[(device, lag["name"])].append(i["name"])
    return {key: sorted(ports) for key, ports in sorted(members.items())}


class Rollups:
    def __init__(self, sessions, nb, prometheus, interval: float = HOUR):
        self.sessions, self.nb, self.prometheus = sessions, nb, prometheus
        self.keep_days = float(os.environ.get("CAPACITY_KEEP_DAYS", 395))
        self.backfill_days = float(os.environ.get("CAPACITY_BACKFILL_DAYS", 15))   # Prometheus' retention
        self.interval = interval
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        from orchestrator import timing

        timing.shared.set(True)   # reads only, so NetBox reads may be shared (this task alone)
        while True:
            try:
                await self.run_once()
            except Exception:   # Prometheus, NetBox or the database away: the next run catches up
                log.exception("capacity: rollup failed")
            # two minutes past the hour, so the hour's last samples are in
            await asyncio.sleep(self.interval - time.time() % self.interval + 120)

    async def read(self, first: float, last: float) -> list[dict]:
        """Rows for the hours ending at `first` .. `last` (whole hours, epoch seconds)."""
        rows: dict[tuple, dict] = {}

        async def take(labels_to_key, expr: str, field: str) -> None:
            for labels, points in await self.prometheus.query_range(expr, first, last, HOUR):
                key = labels_to_key(labels)
                if key is None:
                    continue
                for t, value in points:
                    row = rows.setdefault((*key, t), {"device": key[0], "interface": key[1], "direction": key[2],
                                                      "hour": datetime.fromtimestamp(t - HOUR, timezone.utc)})
                    row[f"{field}_bps"] = value

        for direction in DIRECTIONS:
            for field, expr in _queries(direction).items():
                await take(lambda l, d=direction: (l["device"], l["interface"], d) if "interface" in l else None, expr, field)

        devices = await self.nb.find("dcim/devices")
        interfaces = await self.nb.find("dcim/interfaces")
        for (device, trunk), members in trunk_members(devices, interfaces).items():
            self.prometheus._devices_match([device])   # a name PromQL can take as it is
            selector = f'device="{device}",interface=~"{"|".join(members)}"'
            for direction in DIRECTIONS:
                for field, expr in _queries(direction, selector, total=True).items():
                    await take(lambda l, d=direction, dev=device, tr=trunk: (dev, tr, d), expr, field)
        return [{"avg_bps": None, "p95_bps": None, **row} for row in rows.values()]

    async def run_once(self, now: float | None = None) -> int:
        """Store every complete hour not stored yet; returns the number of rows written."""
        last = int((now or time.time()) // HOUR) * HOUR
        async with self.sessions() as session:
            stored = await session.scalar(select(func.max(TrafficHour.hour)))
        first = last - int(self.backfill_days * 86400) + HOUR
        if stored is not None:
            first = max(first, int(stored.timestamp()) + 2 * HOUR)
        written = 0
        if first <= last:
            rows = await self.read(first, last)
            async with self.sessions() as session:
                for start in range(0, len(rows), 2000):   # Postgres takes at most 32767 parameters
                    result = await session.execute(insert(TrafficHour).values(rows[start:start + 2000])
                                                   .on_conflict_do_nothing())
                    written += result.rowcount
                await session.commit()
        async with self.sessions() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.keep_days)
            await session.execute(delete(TrafficHour).where(TrafficHour.hour < cutoff))
            await session.commit()
        return written


# Forecast: 70 % of the port's speed, a linear trend over the last 4 weeks of
# daily 95th percentiles; shown only with enough data, with its range.
THRESHOLD = 0.7
TREND_DAYS = 28
MIN_DAYS = 7


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]   # nearest rank


def daily_p95(rows: list[dict]) -> dict[date, float]:
    """Each day's 95th percentile of the hourly 95th percentiles, the busier direction's."""
    by_day: dict[tuple[date, str], list[float]] = defaultdict(list)
    for r in rows:
        if r["p95_bps"] is not None:
            by_day[(r["hour"].astimezone(timezone.utc).date(), r["direction"])].append(r["p95_bps"])
    days: dict[date, float] = {}
    for (day, _), values in by_day.items():
        days[day] = max(days.get(day, 0.0), _p95(values))
    return dict(sorted(days.items()))


def forecast(daily: dict[date, float], speed_bps: float | None, today: date, threshold: float = THRESHOLD) -> dict:
    """Days until the trend of `daily` reaches `threshold` of the speed, with a 95 % range from the
    slope's standard error. `status`: no speed, not enough data, over threshold, not rising, rising."""
    points = [((day - today).days, bps) for day, bps in daily.items() if 0 <= (today - day).days < TREND_DAYS]
    result: dict = {"status": "", "days": None, "earliest": None, "latest": None, "days_of_data": len(points),
                    "threshold_bps": threshold * speed_bps if speed_bps else None, "slope_bps_per_day": None,
                    "today_bps": None}
    if not speed_bps:
        return {**result, "status": "no speed"}
    if len(points) < MIN_DAYS:
        return {**result, "status": "not enough data"}
    n = len(points)
    mean_x, mean_y = sum(x for x, _ in points) / n, sum(y for _, y in points) / n
    sxx = sum((x - mean_x) ** 2 for x, _ in points)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / sxx
    level = mean_y - slope * mean_x                        # the trend's value today (x = 0)
    spread = math.sqrt(sum((y - (level + slope * x)) ** 2 for x, y in points) / (n - 2) / sxx)
    target = threshold * speed_bps
    result.update(slope_bps_per_day=slope, today_bps=level)
    if level >= target:
        return {**result, "status": "over threshold", "days": 0, "earliest": 0, "latest": 0}
    if slope <= 0:
        return {**result, "status": "not rising"}
    fast, slow = slope + 1.96 * spread, slope - 1.96 * spread
    return {**result, "status": "rising", "days": round((target - level) / slope),
            "earliest": round((target - level) / fast),
            "latest": round((target - level) / slow) if slow > 0 else None}   # None: may never reach it


async def _rows(sessions, devices: list[str], since: datetime, interface: str | None = None) -> list[dict]:
    query = select(TrafficHour).where(TrafficHour.device.in_(devices), TrafficHour.hour >= since)
    if interface is not None:
        query = query.where(TrafficHour.interface == interface)
    async with sessions() as session:
        found = (await session.scalars(query.order_by(TrafficHour.hour))).all()
    return [{"device": r.device, "interface": r.interface, "direction": r.direction, "hour": r.hour,
             "avg_bps": r.avg_bps, "p95_bps": r.p95_bps} for r in found]


async def _speeds(prometheus, devices: list[str]) -> dict[tuple[str, str], float]:
    found = await prometheus.query(f"fabric_interface_bandwidth_bps{{{prometheus._devices_match(devices)}}}")
    return {(m["device"], m["interface"]): v for m, _, v in found}


def _summary(device: str, interface: str, peak: dict, avg: dict, daily: dict[date, float], speed: float | None,
             today: date) -> dict:
    busiest = max((v for v in peak.values() if v is not None), default=None)
    return {
        "device": device, "interface": interface, "speed_bps": speed or None,
        "in_p95_bps": peak.get("in"), "out_p95_bps": peak.get("out"),
        "in_avg_bps": avg.get("in"), "out_avg_bps": avg.get("out"),
        "p95_percent": 100 * busiest / speed if speed and busiest is not None else None,
        "forecast": forecast(daily, speed, today),
    }


async def _aggregates(sessions, devices: list[str], since: datetime, window_start: datetime,
                      interface: str | None = None):
    """Per port: each direction's peak hourly 95th percentile and mean average since `window_start`,
    and each day's 95th percentile (daily_p95's rule) since `since`. Computed by Postgres (loading every hourly row of 28 days took seconds and held up every other request)."""
    t = TrafficHour
    stored = (t.device.in_(devices), t.hour >= since, *([t.interface == interface] if interface else []))
    recent = t.hour >= window_start
    day = func.date(func.timezone("UTC", t.hour))
    windows = select(t.device, t.interface, t.direction, func.count().filter(recent),
                     func.max(func.coalesce(t.p95_bps, 0)).filter(recent),
                     func.avg(t.avg_bps).filter(recent)).where(*stored).group_by(t.device, t.interface, t.direction)
    days = (select(t.device, t.interface, day, t.direction,
                   func.percentile_disc(0.95).within_group(t.p95_bps))   # nearest rank, as _p95
            .where(*stored, t.p95_bps.is_not(None)).group_by(t.device, t.interface, day, t.direction))
    ports: dict[tuple[str, str], tuple[dict, dict, dict]] = {}
    async with sessions() as session:
        for device, interface, direction, hours, peak, mean in await session.execute(windows):
            p, a, _ = ports.setdefault((device, interface), ({}, {}, {}))
            p[direction] = peak if hours else None
            a[direction] = mean
        for device, interface, on, _direction, value in await session.execute(days):
            daily = ports[(device, interface)][2]
            daily[on] = max(daily.get(on, 0.0), value)   # the busier direction's
    return ports


async def fabric_capacity(sessions, nb, prometheus, site: str, days: int, now: datetime | None = None) -> dict:
    """Every port and trunk of the fabric's active switches with traffic stored: its peak hourly
    95th percentile and average over the last `days`, as a share of its speed, and its forecast;
    busiest first."""
    now = now or datetime.now(timezone.utc)
    devices = sorted(d["name"] for d in await nb.find("dcim/devices", site=site) if status_of(d) == "active")
    if not devices:
        return {"site": site, "days": days, "threshold": THRESHOLD, "ports": []}
    found = await _aggregates(sessions, devices, now - timedelta(days=max(days, TREND_DAYS)), now - timedelta(days=days))
    speeds = await _speeds(prometheus, devices)
    ports = [_summary(device, interface, peak, avg, dict(sorted(daily.items())), speeds.get((device, interface)),
                      now.date())
             for (device, interface), (peak, avg, daily) in found.items()]
    ports.sort(key=lambda p: (-(p["p95_percent"] or -1), p["device"], p["interface"]))
    return {"site": site, "days": days, "threshold": THRESHOLD, "ports": ports}


async def port_capacity(sessions, prometheus, device: str, interface: str, days: int,
                        now: datetime | None = None) -> dict:
    """One port's or trunk's hourly traffic over the last `days`, its daily 95th percentiles and forecast."""
    now = now or datetime.now(timezone.utc)
    rows = await _rows(sessions, [device], now - timedelta(days=max(days, TREND_DAYS)), interface)
    speed = (await _speeds(prometheus, [device])).get((device, interface))
    start = now - timedelta(days=days)
    hours: dict[datetime, dict] = {}
    for r in rows:
        if r["hour"] >= start:
            point = hours.setdefault(r["hour"], {"t": r["hour"].timestamp()})
            point[f"{r['direction']}_avg_bps"], point[f"{r['direction']}_p95_bps"] = r["avg_bps"], r["p95_bps"]
    peak, avg, daily = (await _aggregates(sessions, [device], now - timedelta(days=max(days, TREND_DAYS)), start,
                                          interface)).get((device, interface), ({}, {}, {}))
    return {**_summary(device, interface, peak, avg, dict(sorted(daily.items())), speed, now.date()),
            "days": days, "threshold": THRESHOLD,
            "hours": list(hours.values()),
            "daily": [{"day": d.isoformat(), "p95_bps": v} for d, v in daily_p95(rows).items()]}
