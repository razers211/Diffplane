"""The "what changed" timeline.

An event watcher reads every fabric's state every 30 s (`EVENTS_INTERVAL_SECONDS`) from what the
health scores already read (orchestrator/health.py: collector series, drift, NetBox) plus
Prometheus' own `ALERTS` series, and records each change in the platform database (table
`events`, kept `EVENTS_KEEP_DAYS`, 90). Its first read after a start is a baseline only: nothing
is recorded for states that were already there. A state that disappears (a switch no longer
polled, a peer removed) records nothing, except an alert, which then counts as resolved.

Links: ports in use, Eth-Trunks and MEth only; optic levels only on ports in use.
Deploys and intent changes are not recorded here: `GET /events` merges them in from the audit trail.
"""

import asyncio
import copy
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from orchestrator import health
from orchestrator.db import AuditEvent, Event, Job
from orchestrator.routing_intent import status_of

log = logging.getLogger("orchestrator.events")

MGMT = "MEth0/0/0"
# Audit actions that are not changes to a fabric (sign-ins, accounts, settings): never on the timeline.
NOT_CHANGES = re.compile(r"^(login|user\.|settings\.|directory\.)")


@dataclass(frozen=True)
class State:
    site: str | None
    device: str | None
    kind: str
    subject: str
    state: str
    bad: bool
    detail: str | None = None
    score: int | None = None   # health: a lower score is worse, a higher one better


def states(fabric: str, f: health.Facts, view: dict) -> dict[tuple, State]:
    """Every watched state of one fabric now, by a stable key."""
    found: dict[tuple, State] = {}

    def put(device: str | None, kind: str, subject: str, state: str, bad: bool, detail: str | None = None,
            score: int | None = None):
        found[(fabric, device, kind, subject)] = State(fabric, device, kind, subject, state, bad, detail, score)

    for name in sorted(f.devices):
        if not f.active(name):
            continue
        polled = name in f.collected
        put(name, "polling", "collector", "polled" if polled else "not polled", not polled)
        if name in f.drift:
            put(name, "drift", "intent", f.drift[name], f.drift[name] in ("out_of_sync", "error"))
        if not polled:
            continue    # its other series are gone; nothing recorded for what disappears
        for port in sorted(f.in_use.get(name, set()) | {MGMT}):
            if (up := f.oper.get((name, port))) is not None:
                put(name, "link", port, "up" if up else "down", not up and (name, port) not in f.expected_down)
        plane = f.control.get(name) or {}
        for p in plane.get("bgp", []):
            subject = f"BGP {p['peer']} {p['family']}" + (f" in {p['vpn']}" if p["vpn"] != "_public_" else "")
            put(name, "bgp", subject, p["state"], not p["up"] and not p["expected_down"], p.get("switch"))
        for o in plane.get("ospf", []):
            put(name, "ospf", f"OSPF {o['neighbor']} on {o['interface']}", o["state"], not o["up"])
        if mlag := plane.get("mlag"):
            if mlag["heartbeat"] is not None:
                put(name, "mlag", "M-LAG heartbeat", "ok" if mlag["heartbeat"] else "lost",
                    not mlag["heartbeat"] and not mlag["expected_down"])
            for link, up in mlag["peer_links"].items():
                put(name, "mlag", f"peer-link {link}", "up" if up else "down", not up and not mlag["expected_down"])
        hw = f.hardware.get(name) or {}
        for p in hw.get("power", []):
            put(name, "hardware", f"power module {p['module']}", (p["status"] or "?").lower(), not p["ok"])
        for fan in hw.get("fans", []):
            put(name, "hardware", f"fan {fan['fan']}", "present" if fan["present"] else "missing", not fan["present"])
        for s in hw.get("sensors", []):
            t = s["thresholds"]
            level = ("major" if "major" in t and s["celsius"] >= t["major"]
                     else "minor" if "minor" in t and s["celsius"] >= t["minor"] else "ok" if s["normal"] is not False else "not normal")
            put(name, "hardware", f"temperature {s['sensor']} slot {s['slot']}", level, level != "ok", f"{s['celsius']:g} °C")
        for kind in ("cpu", "memory"):
            for row in hw.get(kind, []):
                put(name, "hardware", f"{kind.upper() if kind == 'cpu' else kind} slot {row['slot']}",
                    "ok" if row["ok"] else "over threshold", not row["ok"], f"{row['usage']:g} %")
        for optic in hw.get("optics", []):
            if optic["in_use"]:
                put(name, "optic", f"optic {optic['port']}", optic["level"], optic["level"] != "ok", "; ".join(optic["reasons"]) or None)
    # Every score change is an event (the reasons changed), not only a change of level.
    for h in view["switches"]:
        put(h["name"], "health", "score", f"{h['level']} {h['score']}", h["level"] != "good", _score_text(h), h["score"])
    put(None, "health", "fabric score", f"{view['level']} {view['score']}", view["level"] != "good", _score_text(view),
        view["score"])
    return found


def _score_text(h: dict) -> str:
    reasons = "; ".join(r["text"] for r in h["reasons"][:3]) + ("; …" if len(h["reasons"]) > 3 else "")
    return f"{h['score']}" + (f": {reasons}" if reasons else "")


def alert_states(alerts: list[tuple[dict, float, float]], site_of: dict[str, str]) -> dict[tuple, State]:
    """Firing alerts (Prometheus' ALERTS series), by alert name and labels."""
    found = {}
    for labels, _, _ in alerts:
        if labels.get("alertstate") != "firing":
            continue
        device = labels.get("device")
        extra = ", ".join(f"{k}={v}" for k, v in sorted(labels.items())
                          if k not in ("__name__", "alertname", "alertstate", "device", "severity", "job", "instance"))
        subject = labels.get("alertname", "?") + (f" ({extra})" if extra else "")
        site = site_of.get(device) if device else None
        found[(site, device, "alert", subject)] = State(site, device, "alert", subject, "firing", True, labels.get("severity"))
    return found


def transitions(previous: dict[tuple, State], current: dict[tuple, State]) -> list[dict]:
    """Events between two reads: changed states, new states, and alerts that stopped firing."""
    events = []
    for key, now in current.items():
        before = previous.get(key)
        if before is not None and before.state == now.state:
            continue
        if before is None and not now.bad and now.kind != "alert":
            continue          # something new that is fine (a new port, a new peer up): not news
        if before is not None and before.score is not None and now.score is not None:
            severity = "problem" if now.score < before.score else "recovered"
        else:
            severity = "problem" if now.bad else "recovered" if before is not None and before.bad else "change"
        events.append({"site": now.site, "device": now.device, "kind": now.kind, "subject": now.subject,
                       "state": now.state, "previous": before.state if before else None, "severity": severity,
                       "detail": now.detail})
    for key, before in previous.items():
        if before.kind == "alert" and key not in current:
            events.append({"site": before.site, "device": before.device, "kind": "alert", "subject": before.subject,
                           "state": "resolved", "previous": "firing", "severity": "recovered", "detail": before.detail})
    return events


class EventStore:
    def __init__(self, sessions):
        self.sessions = sessions

    async def record(self, events: list[dict]) -> None:
        if not events:
            return
        async with self.sessions() as session:
            session.add_all(Event(**e) for e in events)
            await session.commit()

    async def purge(self, keep_days: float) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        async with self.sessions() as session:
            result = await session.execute(delete(Event).where(Event.at < cutoff))
            await session.commit()
            return result.rowcount or 0

    async def timeline(self, sites: set[str] | None, device: str | None = None, kind: str | None = None,
                       since: datetime | None = None, until: datetime | None = None, limit: int = 200,
                       names: set[str] | None = None) -> list[dict]:
        """Events and fabric changes (deploys, intent) newest first. `sites`: the fabrics to show
        (None: all); `names`: those fabrics' switch names, to place audit entries without a job."""
        limit = min(max(limit, 1), 1000)
        query = select(Event).order_by(Event.at.desc(), Event.id.desc()).limit(limit)
        if sites is not None:
            query = query.where(Event.site.in_(sites) | (Event.site.is_(None) & (Event.kind == "alert")))
        if device:
            query = query.where(Event.device == device)
        if kind and kind != "change":
            query = query.where(Event.kind == kind)
        if since:
            query = query.where(Event.at >= since)
        if until:
            query = query.where(Event.at <= until)
        rows: list[dict] = []
        async with self.sessions() as session:
            if kind != "change":
                rows += [{"at": e.at.isoformat(), "site": e.site, "device": e.device, "kind": e.kind, "subject": e.subject,
                          "state": e.state, "previous": e.previous, "severity": e.severity, "detail": e.detail,
                          "user": None, "job_id": None} for e in (await session.scalars(query)).all()]
            if kind in (None, "change"):
                audit = select(AuditEvent).order_by(AuditEvent.at.desc(), AuditEvent.id.desc()).limit(limit * 3)
                if since:
                    audit = audit.where(AuditEvent.at >= since)
                if until:
                    audit = audit.where(AuditEvent.at <= until)
                entries = [a for a in (await session.scalars(audit)).all() if not NOT_CHANGES.match(a.action)]
                job_sites = dict((await session.execute(select(Job.id, Job.site).where(
                    Job.id.in_({a.job_id for a in entries if a.job_id})))).all())
                for a in entries:
                    words = set(re.split(r"[\s:=,+/]+", a.detail or ""))
                    site = job_sites.get(a.job_id) if a.job_id else None
                    if sites is not None and not (site in sites if a.job_id else words & ((names or set()) | sites)):
                        continue
                    if device and device not in words:
                        continue
                    rows.append({"at": a.at.isoformat(), "site": site, "device": None, "kind": "change",
                                 "subject": a.action, "state": "", "previous": None, "severity": "change",
                                 "detail": a.detail, "user": a.username, "job_id": a.job_id})
        return sorted(rows, key=lambda r: r["at"], reverse=True)[:limit]


class EventWatcher:
    """Reads every fabric's state every `interval` seconds and records what changed."""

    def __init__(self, store: EventStore, nb, prometheus, drift_report, max_age: float, interval: float | None = None):
        """`drift_report`: an async callable giving the drift report; `max_age`: how recent a poll
        must be for a switch to count as polled."""
        self.store, self.nb, self.prometheus, self.drift_report = store, nb, prometheus, drift_report
        self.max_age = max_age
        self.interval = interval or float(os.environ.get("EVENTS_INTERVAL_SECONDS", 30))
        self.keep_days = float(os.environ.get("EVENTS_KEEP_DAYS", 90))
        self.previous: dict[tuple, State] | None = None   # None until the baseline read
        self.last_purge = 0.0
        self.latest: dict[str, tuple[float, dict]] = {}   # fabric -> (when, its health), for the GUI
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
            started = time.monotonic()
            try:
                await self.run_once()
            except Exception:   # Prometheus or NetBox away: try again next time, baseline kept
                log.exception("event watcher: read failed")
            # A read starts every interval: its shared NetBox reads are then refreshed
            # before they are 30 s old, so pages never wait for them.
            await asyncio.sleep(max(1.0, self.interval - (time.monotonic() - started)))

    async def read(self) -> dict[tuple, State]:
        from orchestrator.inventory import SITE as INVENTORY

        devices = await self.nb.find("dcim/devices")
        fabrics = sorted({(d.get("site") or {}).get("slug") for d in devices
                          if status_of(d) == "active"} - {None, INVENTORY})
        site_of = {d["name"]: (d.get("site") or {}).get("slug") for d in devices}
        report = await self.drift_report()
        current: dict[tuple, State] = {}
        for fabric in fabrics:
            f = await health.facts(self.nb, self.prometheus, fabric, report, self.max_age)
            scores = health.fabric_health(fabric, f)
            self.latest[fabric] = (time.monotonic(), scores)
            current.update(states(fabric, f, scores))
        current.update(alert_states(await self.prometheus.query('ALERTS{alertstate="firing"}'), site_of))
        return current

    def recent(self, fabric: str) -> dict | None:
        """The fabric's health from the last read, if it is at most two intervals old (the
        GUI asks often and a fresh computation takes seconds on a real fabric)."""
        found = self.latest.get(fabric)
        if found is None or time.monotonic() - found[0] > 2 * self.interval + 15:
            return None
        return copy.deepcopy(found[1])

    async def run_once(self) -> int:
        current = await self.read()
        events = transitions(self.previous, current) if self.previous is not None else []
        self.previous = current
        await self.store.record(events)
        if time.monotonic() - self.last_purge > 3600:
            self.last_purge = time.monotonic()
            await self.store.purge(self.keep_days)
        return len(events)
