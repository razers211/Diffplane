"""Diff jobs: run `annet diff` per device in the background and store the results."""

import asyncio
import logging

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator.annet import AnnetError, AnnetRunner
from orchestrator.db import DiffSnapshot, Job, now
from orchestrator.netbox import NetBoxClient
from orchestrator.redact import redact

log = logging.getLogger(__name__)

# A diff job is started by a user ("diff") or by the drift scheduler ("drift").
DIFF_KINDS = ("diff", "drift")


class JobError(Exception):
    pass


class DiffJobs:
    def __init__(self, sessions: async_sessionmaker, annet: AnnetRunner, netbox: NetBoxClient):
        self.sessions = sessions
        self.annet = annet
        self.netbox = netbox
        self._tasks: set[asyncio.Task] = set()
        # after a drift run diffs a device, hand-made routing objects are imported (set by the app).
        self.importer = None

    async def recover(self) -> None:
        """Jobs left running by a previous orchestrator process cannot finish: fail them."""
        async with self.sessions() as session:
            await session.execute(
                update(Job)
                .where(Job.status == "running")
                .values(status="failed", finished_at=now(), error="orchestrator restarted during the job")
            )
            await session.execute(
                update(DiffSnapshot)
                .where(DiffSnapshot.status == "pending")
                .values(status="error", finished_at=now(), error="orchestrator restarted during the job")
            )
            await session.commit()

    async def targets(self, site: str | None, devices: list[str] | None) -> list[str]:
        """Active devices with a management address; planned devices cannot be fetched."""
        found = await self.netbox.find("dcim/devices", status="active", has_primary_ip="true",
                                       **({"site": site} if site else {}))
        active = sorted(d["name"] for d in found)
        if devices is None:
            return active
        unknown = sorted(set(devices) - set(active))
        if unknown:
            raise JobError(f"not active devices with a management address: {', '.join(unknown)}")
        return sorted(set(devices))

    async def start(self, site: str | None = None, devices: list[str] | None = None, kind: str = "diff",
                    requested_by: str | None = None) -> int:
        assert kind in DIFF_KINDS
        targets = await self.targets(site, devices)
        if not targets:
            raise JobError("no active devices in scope")
        async with self.sessions() as session:
            job = Job(kind=kind, status="running", site=site, requested_by=requested_by)
            job.snapshots = [DiffSnapshot(device=name, status="pending") for name in targets]
            session.add(job)
            await session.commit()
            job_id = job.id
        task = asyncio.create_task(self._run(job_id, targets, kind))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job_id

    def running(self) -> bool:
        return bool(self._tasks)

    async def wait(self) -> None:
        """Wait for running jobs (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _run(self, job_id: int, devices: list[str], kind: str = "diff") -> None:
        try:
            for device in devices:  # one at a time: each diff opens an SSH session
                await self._diff_one(job_id, device)
                if kind == "drift" and self.importer is not None:
                    await self.importer.run(device)   # best effort; never raises
            status, error = "done", None
        except Exception as exc:  # never leave a job "running"
            log.exception("diff job %s failed", job_id)
            status, error = "failed", redact(str(exc))
        async with self.sessions() as session:
            await session.execute(
                update(Job).where(Job.id == job_id).values(status=status, finished_at=now(), error=error)
            )
            await session.commit()

    async def _diff_one(self, job_id: int, device: str) -> None:
        try:
            text = await self.annet.diff(device)
            values = {"status": "ok", "has_changes": bool(text), "diff": redact(text)}
        except AnnetError as exc:
            values = {"status": "error", "error": redact(str(exc))}
        async with self.sessions() as session:
            await session.execute(
                update(DiffSnapshot)
                .where(DiffSnapshot.job_id == job_id, DiffSnapshot.device == device)
                .values(finished_at=now(), **values)
            )
            await session.commit()


def snapshot_json(s: DiffSnapshot, job_kind: str | None = None) -> dict:
    return {
        "device": s.device,
        "status": s.status,
        "has_changes": s.has_changes,
        "diff": s.diff,
        "error": s.error,
        "created_at": s.created_at.isoformat(),
        "finished_at": s.finished_at.isoformat() if s.finished_at else None,
        "job_id": s.job_id,
        # "diff" or "drift" (a diff job), or "deploy" (recorded when a deploy was verified)
        "job_kind": job_kind,
    }


def job_json(job: Job, with_snapshots: bool = False) -> dict:
    snaps = job.snapshots
    body = {
        "id": job.id,
        "kind": job.kind,
        "requested_by": job.requested_by,
        "status": job.status,
        "site": job.site,
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error": job.error,
        "devices": len(snaps),
        "changed": sum(1 for s in snaps if s.has_changes),
        "errors": sum(1 for s in snaps if s.status == "error"),
    }
    if with_snapshots:
        body["snapshots"] = [snapshot_json(s, job.kind) for s in snaps]
    return body


async def list_jobs(sessions: async_sessionmaker, limit: int) -> list[dict]:
    async with sessions() as session:
        jobs = (await session.scalars(
            select(Job).where(Job.kind.in_(DIFF_KINDS)).order_by(Job.id.desc()).limit(limit)
        )).all()
        return [job_json(j) for j in jobs]


async def get_job(sessions: async_sessionmaker, job_id: int) -> dict | None:
    async with sessions() as session:
        job = await session.get(Job, job_id)
        return job_json(job, with_snapshots=True) if job and job.kind in DIFF_KINDS else None


async def latest_snapshot(sessions: async_sessionmaker, device: str) -> dict | None:
    async with sessions() as session:
        snap = await session.scalar(
            select(DiffSnapshot)
            .options(selectinload(DiffSnapshot.job))
            .where(DiffSnapshot.device == device, DiffSnapshot.status != "pending")
            .order_by(DiffSnapshot.id.desc())
            .limit(1)
        )
        return snapshot_json(snap, snap.job.kind) if snap else None


async def latest_snapshots(sessions: async_sessionmaker, devices: list[str]) -> dict[str, DiffSnapshot]:
    """Each device's most recent finished snapshot, from any diff, drift or deploy job
    (with its job loaded)."""
    if not devices:
        return {}
    async with sessions() as session:
        newest = (
            select(func.max(DiffSnapshot.id))
            .where(DiffSnapshot.device.in_(devices), DiffSnapshot.status != "pending")
            .group_by(DiffSnapshot.device)
        )
        snaps = (await session.scalars(
            select(DiffSnapshot).options(selectinload(DiffSnapshot.job)).where(DiffSnapshot.id.in_(newest))
        )).all()
        return {s.device: s for s in snaps}
