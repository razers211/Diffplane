"""Scheduled drift check: a fabric-wide `annet diff` every DRIFT_INTERVAL_SECONDS.

Read-only on the switches (the same diff jobs a user can start). A device is "out of
sync" when its latest diff is not empty: a manual change on the switch, or intent not
deployed yet; the platform does not guess which. A verified deploy records the device's
empty diff too, so drift clears without waiting for the next run. Runs are skipped while
a deploy rolls out (a half-deployed fabric is not drift) or while another diff job runs.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

from prometheus_client import CollectorRegistry, Gauge, generate_latest

from orchestrator import deploys
from orchestrator.jobs import DiffJobs, JobError, latest_snapshots
from orchestrator.netbox import NetBoxError

log = logging.getLogger(__name__)

DEFAULT_INTERVAL = 900.0   # every 15 minutes
# After a skipped or failed run, try again this soon rather than a whole interval later:
# drift resumes shortly after a rollout ends, and a skip at start-up (NetBox not answering
# yet) does not leave the fabric unchecked long enough to fire FabricDriftCheckStopped.
RETRY_SECONDS = 60.0


def interval_from_env() -> float:
    return float(os.environ.get("DRIFT_INTERVAL_SECONDS", DEFAULT_INTERVAL))


@dataclass
class SchedulerState:
    interval: float
    last_started: float | None = None
    last_job_id: int | None = None
    last_skip: str | None = None
    next_run: float | None = None
    history: list[str] = field(default_factory=list)


class DriftScheduler:
    def __init__(self, jobs: DiffJobs, interval: float | None = None):
        self.jobs = jobs
        self.state = SchedulerState(interval=interval or interval_from_env())
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def next_delay(self, started: int | None) -> float:
        """Seconds until the next attempt: a full interval after a run, sooner after a skip."""
        return self.state.interval if started is not None else min(self.state.interval, RETRY_SECONDS)

    async def _loop(self) -> None:
        while True:
            started = None
            try:
                started = await self.run_once()
            except Exception:   # the scheduler must outlive one bad run
                log.exception("drift run failed")
            delay = self.next_delay(started)
            self.state.next_run = time.time() + delay
            await asyncio.sleep(delay)

    async def run_once(self) -> int | None:
        """Start one drift job unless something makes the result meaningless."""
        reason, unexpected = None, False
        if (job_id := await deploys.rolling_out(self.jobs.sessions)) is not None:
            reason = f"deploy #{job_id} is rolling out"
        elif self.jobs.running():
            reason = "another diff job is running"
        if reason is None:
            try:
                started = await self.jobs.start(kind="drift", requested_by="scheduler")
            except (JobError, NetBoxError) as exc:
                reason, unexpected = str(exc), True
            else:
                self.state.last_started, self.state.last_job_id, self.state.last_skip = time.time(), started, None
                return started
        self.state.last_skip = reason
        # A rollout or another diff job is normal; anything else is worth seeing in the logs.
        (log.warning if unexpected else log.info)("drift run skipped: %s", reason)
        return None


async def drift_report(jobs: DiffJobs, devices: list[dict]) -> list[dict]:
    """Per active device: latest diff result. devices: NetBox device objects."""
    names = [d["name"] for d in devices]
    snaps = await latest_snapshots(jobs.sessions, names)
    report = []
    for d in sorted(devices, key=lambda d: d["name"]):
        snap = snaps.get(d["name"])
        if snap is None:
            state = "unknown"
        elif snap.status == "error":
            state = "error"
        else:
            state = "out_of_sync" if snap.has_changes else "in_sync"
        report.append({
            "device": d["name"],
            "site": (d.get("site") or {}).get("slug") if isinstance(d.get("site"), dict) else d.get("site"),
            "state": state,
            "checked_at": snap.finished_at.isoformat() if snap and snap.finished_at else None,
            "job_id": snap.job_id if snap else None,
            "job_kind": snap.job.kind if snap else None,
            "diff": snap.diff if snap else None,
            "error": snap.error if snap else None,
        })
    return report


STATES = ("in_sync", "out_of_sync", "error", "unknown")


def metrics(report: list[dict], scheduler: SchedulerState | None) -> bytes:
    """Prometheus exposition of the drift report (scraped from the orchestrator's /metrics)."""
    registry = CollectorRegistry()
    device_state = Gauge("fabric_drift_device_state", "1 for the device's current drift state",
                         ["device", "site", "state"], registry=registry)
    out_of_sync = Gauge("fabric_drift_out_of_sync_devices", "Devices whose latest diff is not empty",
                        registry=registry)
    for row in report:
        for state in STATES:
            device_state.labels(row["device"], row["site"] or "", state).set(1 if row["state"] == state else 0)
    out_of_sync.set(sum(1 for r in report if r["state"] == "out_of_sync"))
    if scheduler is not None:
        Gauge("fabric_drift_interval_seconds", "Drift check interval", registry=registry).set(scheduler.interval)
        # Absent until a run has started (an unset gauge would read 0, i.e. 1970); the
        # FabricDriftCheckStopped alert treats absence as stopped (deploy/prometheus/alerts.yml).
        # With no active switch there is nothing to check: not a stopped check (a new install or demo).
        last = scheduler.last_started or (None if report else time.time())
        if last:
            Gauge("fabric_drift_last_run_timestamp_seconds", "When the last drift job started",
                  registry=registry).set(last)
    return generate_latest(registry)
