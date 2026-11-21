"""Traffic anomalies and flapping links, from data the platform already has.

A port's or trunk's usual traffic is the same hour of day (UTC) over the last 14 days of hourly
averages (`traffic_hourly`): their median and spread (1.4826 x the median absolute deviation),
with at least 7 days of that hour. Every 5 minutes the last 15 minutes' average rate (Prometheus; a
trunk: the sum of its members) is an anomaly, a spike or a drop, only when it is more than 4 spreads
from the median, more than 1 % of the speed the switch reports (`ANOMALY_MIN_SHARE`), and more than
10 Mbit/s away (`ANOMALY_MIN_BPS`); both are lowered only for a lab test. A port in use, trunk or MEth that went down 3 times within 30 minutes on the
timeline (events) is flapping. Both are exported on the orchestrator's `/metrics` for the alert
rules, and so reach the timeline as alerts; health takes -20 for a flapping link (orchestrator/health.py).
"""

import asyncio
import logging
import os
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from orchestrator.capacity import DIRECTIONS, trunk_members
from orchestrator.db import Event, TrafficHour

log = logging.getLogger("orchestrator.anomaly")

WINDOW_DAYS, MIN_DAYS = 14, 7
SPREADS, MIN_SHARE = 4, 0.01
FLAP_DOWNS, FLAP_MINUTES = 3, 30
MAD_TO_SPREAD = 1.4826   # a normal distribution's standard deviation from its median absolute deviation


def baselines(rows: list[dict]) -> dict[tuple[str, str, str, int], tuple[float, float]]:
    """(device, interface, direction, hour of day) -> (median, spread) of the hourly averages."""
    values: dict[tuple, list[float]] = defaultdict(list)
    for r in rows:
        if r["avg_bps"] is not None:
            values[(r["device"], r["interface"], r["direction"], r["hour"].astimezone(timezone.utc).hour)].append(r["avg_bps"])
    found = {}
    for key, xs in values.items():
        if len(xs) >= MIN_DAYS:
            median = statistics.median(xs)
            found[key] = (median, MAD_TO_SPREAD * statistics.median(abs(x - median) for x in xs))
    return found


def judge(value: float, baseline: tuple[float, float], speed: float | None, min_bps: float,
          min_share: float = MIN_SHARE) -> str | None:
    """"high", "low" or None: far from usual on all three counts."""
    median, spread = baseline
    away = value - median
    if abs(away) <= SPREADS * spread or abs(away) <= min_bps or (speed and abs(away) <= min_share * speed):
        return None
    return "high" if away > 0 else "low"


def flapping(downs: list[tuple[str, str, datetime]], now: datetime) -> dict[tuple[str, str], int]:
    """(device, port) -> downs within the last 30 minutes, for ports down at least 3 times."""
    since = now - timedelta(minutes=FLAP_MINUTES)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for device, port, at in downs:
        if at >= since:
            counts[(device, port)] += 1
    return {key: n for key, n in sorted(counts.items()) if n >= FLAP_DOWNS}


class Anomalies:
    """Reads every 5 minutes; `metrics()` gives the latest result to Prometheus."""

    def __init__(self, sessions, nb, prometheus, interval: float = 300):
        self.sessions, self.nb, self.prometheus, self.interval = sessions, nb, prometheus, interval
        # Both lowered only for a lab test (a 100 Mbit/s MEth needs over 1 Mbit/s more, a load that once
        # made NETCONF time out on a test switch).
        self.min_bps = float(os.environ.get("ANOMALY_MIN_BPS") or 10e6)
        self.min_share = float(os.environ.get("ANOMALY_MIN_SHARE") or MIN_SHARE)
        self.found: list[dict] = []
        self.flaps: dict[tuple[str, str], int] = {}
        self._baselines: dict = {}
        self._baseline_hour: int | None = None
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
            except Exception:   # Prometheus, NetBox or the database away: the last result stays
                log.exception("anomaly: read failed")
            await asyncio.sleep(self.interval)

    async def _rows(self, since: datetime) -> list[dict]:
        async with self.sessions() as session:
            found = (await session.scalars(select(TrafficHour).where(TrafficHour.hour >= since))).all()
        return [{"device": r.device, "interface": r.interface, "direction": r.direction, "hour": r.hour,
                 "avg_bps": r.avg_bps} for r in found]

    async def current(self) -> dict[tuple[str, str, str], float]:
        """(device, interface, direction) -> average bits/s over the last 15 minutes; trunks summed."""
        rates: dict[tuple[str, str, str], float] = {}
        for direction in DIRECTIONS:
            for labels, _, value in await self.prometheus.query(f"rate(fabric_interface_{direction}_octets_total[15m]) * 8"):
                if "interface" in labels:
                    rates[(labels["device"], labels["interface"], direction)] = value
        devices, interfaces = await asyncio.gather(self.nb.find("dcim/devices"), self.nb.find("dcim/interfaces"))
        for (device, trunk), members in trunk_members(devices, interfaces).items():
            for direction in DIRECTIONS:
                parts = [rates[(device, m, direction)] for m in members if (device, m, direction) in rates]
                if parts:
                    rates[(device, trunk, direction)] = sum(parts)
        return rates

    async def run_once(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        if self._baseline_hour != int(now.timestamp() // 3600):   # the rollups change once an hour
            self._baselines = baselines(await self._rows(now - timedelta(days=WINDOW_DAYS)))
            self._baseline_hour = int(now.timestamp() // 3600)
        speeds = {(m["device"], m["interface"]): v for m, _, v in await self.prometheus.query("fabric_interface_bandwidth_bps")}
        found = []
        for (device, interface, direction), value in sorted((await self.current()).items()):
            base = self._baselines.get((device, interface, direction, now.hour))
            kind = judge(value, base, speeds.get((device, interface)), self.min_bps, self.min_share) if base else None
            if kind:
                found.append({"device": device, "interface": interface, "direction": direction, "kind": kind,
                              "bps": value, "usual_bps": base[0]})
        async with self.sessions() as session:
            downs = (await session.execute(select(Event.device, Event.subject, Event.at).where(
                Event.kind == "link", Event.state == "down", Event.at >= now - timedelta(minutes=FLAP_MINUTES)))).all()
        self.found, self.flaps = found, flapping([tuple(d) for d in downs], now)
        return found

    def metrics(self) -> bytes:
        from prometheus_client import CollectorRegistry, Gauge, generate_latest

        registry = CollectorRegistry()
        traffic = Gauge("fabric_traffic_anomaly", "Bits/s over the last 15 minutes of a port or trunk far from its "
                        "usual level for this hour (only while it is)", ["device", "interface", "direction", "kind"],
                        registry=registry)
        usual = Gauge("fabric_traffic_usual_bps", "The usual traffic (median of the same hour over 14 days) of an "
                      "anomalous port or trunk", ["device", "interface", "direction"], registry=registry)
        flaps = Gauge("fabric_link_flapping", "Downs of a port in use, trunk or MEth within the last 30 minutes, "
                      "while it is flapping (3 or more)", ["device", "interface"], registry=registry)
        for a in self.found:
            traffic.labels(a["device"], a["interface"], a["direction"], a["kind"]).set(a["bps"])
            usual.labels(a["device"], a["interface"], a["direction"]).set(a["usual_bps"])
        for (device, port), n in self.flaps.items():
            flaps.labels(device, port).set(n)
        return generate_latest(registry)


def report(reader: "Anomalies", device: str, rates: dict, speeds: dict, now: datetime) -> list[str]:
    """One line per port or trunk of `device`: now, usual for this hour, and the verdict."""
    mbps = lambda bps: f"{bps / 1e6:.3f} Mbit/s"

    lines, idle = [], 0
    for (dev, interface, direction), value in sorted(rates.items()):
        if dev != device:
            continue
        base = reader._baselines.get((dev, interface, direction, now.hour))
        if value < 1 and (base is None or base[0] < 1):   # idle now and usually: can never alarm
            idle += 1
            continue
        if base is None:
            lines.append(f"{interface:12} {direction:3}  now {mbps(value):>12}  no baseline (needs {MIN_DAYS} days of this hour)")
            continue
        kind = judge(value, base, speeds.get((dev, interface)), reader.min_bps, reader.min_share)
        lines.append(f"{interface:12} {direction:3}  now {mbps(value):>12}  usual {mbps(base[0]):>12} "
                     f"± {mbps(base[1]):>10}  {kind or 'as usual'}")
    if idle:
        lines.append(f"({idle} idle port directions left out: no traffic now or usually)")
    return lines


async def _check(device: str) -> int:
    """Read-only: the baselines of this hour and the last 15 minutes of one switch, judged now."""
    from orchestrator import db
    from orchestrator.config import Settings
    from orchestrator.netbox import NetBoxClient
    from orchestrator.prometheus import PrometheusClient

    settings = Settings.from_env()
    sessions = db.make_sessionmaker(db.make_engine(settings.database_url, pooled=False))
    reader = Anomalies(sessions, NetBoxClient(settings.netbox_url, settings.netbox_token, settings.netbox_timeout),
                       PrometheusClient(settings.prometheus_url))
    now = datetime.now(timezone.utc)
    await reader.run_once(now)
    speeds = {(m["device"], m["interface"]): v for m, _, v in await reader.prometheus.query("fabric_interface_bandwidth_bps")}
    print(f"{device} at {now:%H:%M} UTC, against {now.hour:02d}:00 over the last {WINDOW_DAYS} days "
          f"(minimum {reader.min_bps / 1e6:g} Mbit/s and {reader.min_share:.0%} of speed):")
    print("\n".join(report(reader, device, await reader.current(), speeds, now)) or "no traffic read")
    print(f"flapping: {', '.join(f'{d} {p} ({n})' for (d, p), n in reader.flaps.items()) or 'none'}")
    return 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        sys.exit("usage: python -m orchestrator.anomaly <switch>")
    sys.exit(asyncio.run(_check(sys.argv[1])))
