"""Diff jobs against a real PostgreSQL (pgserver, or the system binaries: tests/pg.py), schema from the Alembic migration."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator import db
from orchestrator.annet import AnnetError
from orchestrator.app import app, get_jobs
from orchestrator.jobs import DiffJobs, JobError, get_job, latest_snapshot, list_jobs
from tests.helpers import dc2_data, seed
from seed.loader import Seeder
from seed.model import load, parse
from tests.seed.fake_netbox import FakeNetBox
from tests import pg

pg.require()
ROOT = Path(__file__).resolve().parents[2]


class FakeAnnet:
    def __init__(self, results: dict[str, str | Exception]):
        self.results = results
        self.calls: list[str] = []

    async def diff(self, device: str) -> str:
        self.calls.append(device)
        result = self.results.get(device, "")
        if isinstance(result, Exception):
            raise result
        return result


def example_and_dc2() -> FakeNetBox:
    """The small example with leaf1 active, and dc2 all planned."""
    nb = FakeNetBox()
    asyncio.run(seed(nb, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    asyncio.run(Seeder(nb, parse(dc2_data())).run())
    return nb


def run(coro):
    return asyncio.run(coro)


def make_jobs(database_url, annet) -> DiffJobs:
    engine = db.make_engine(database_url, pooled=False)
    return DiffJobs(db.make_sessionmaker(engine), annet, example_and_dc2())


def test_migration_creates_tables(database_url):
    async def tables():
        engine = db.make_engine(database_url, pooled=False)
        async with engine.connect() as conn:
            rows = await conn.exec_driver_sql("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            names = {r[0] for r in rows}
        await engine.dispose()
        return names

    assert {"jobs", "diff_snapshots", "alembic_version"} <= run(tables())


def test_diff_job_stores_one_snapshot_per_active_device(database_url, monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", "ANNET-TEST")
    annet = FakeAnnet({"leaf1": "+ snmp-agent community read ANNET-TEST alias ro-1\n"})

    jobs = make_jobs(database_url, annet)

    async def scenario():
        job_id = await jobs.start(site="example")
        await jobs.wait()
        return await get_job(jobs.sessions, job_id)

    job = run(scenario())

    assert annet.calls == ["leaf1"]   # planned leaf2/spine1 are not fetched
    assert job["status"] == "done"
    assert (job["devices"], job["changed"], job["errors"]) == (1, 1, 0)
    (snap,) = job["snapshots"]
    assert snap["device"] == "leaf1" and snap["status"] == "ok" and snap["has_changes"] is True
    assert snap["diff"] == "+ snmp-agent community read ****** alias ro-1\n"


def test_empty_diff_is_recorded_as_no_changes(database_url):
    jobs = make_jobs(database_url, FakeAnnet({"leaf1": ""}))

    async def scenario():
        await jobs.start(devices=["leaf1"])
        await jobs.wait()
        return await latest_snapshot(jobs.sessions, "leaf1")

    snap = run(scenario())

    assert snap["status"] == "ok" and snap["has_changes"] is False and snap["diff"] == ""


def test_annet_failure_is_recorded_per_device(database_url):
    jobs = make_jobs(database_url, FakeAnnet({"leaf1": AnnetError("annet diff failed: timeout")}))

    async def scenario():
        job_id = await jobs.start(site="example")
        await jobs.wait()
        return await get_job(jobs.sessions, job_id)

    job = run(scenario())

    assert job["status"] == "done" and job["errors"] == 1
    assert job["snapshots"][0]["error"] == "annet diff failed: timeout"


def test_planned_or_unknown_devices_are_refused(database_url):
    jobs = make_jobs(database_url, FakeAnnet({}))

    with pytest.raises(JobError, match="spine1"):
        run(jobs.start(devices=["leaf1", "spine1"]))
    with pytest.raises(JobError, match="no active devices"):
        run(jobs.start(site="dc2"))


def test_restart_fails_jobs_left_running(database_url):
    jobs = make_jobs(database_url, FakeAnnet({}))

    async def scenario():
        async with jobs.sessions() as session:
            job = db.Job(kind="diff", status="running", site="example")
            job.snapshots = [db.DiffSnapshot(device="leaf1", status="pending")]
            session.add(job)
            await session.commit()
            job_id = job.id
        await jobs.recover()
        return await get_job(jobs.sessions, job_id)

    job = run(scenario())

    assert job["status"] == "failed" and "restarted" in job["error"]
    assert job["snapshots"][0]["status"] == "error"


def test_jobs_are_listed_newest_first(database_url):
    jobs = make_jobs(database_url, FakeAnnet({}))

    async def scenario():
        for _ in range(3):
            await jobs.start(site="example")
        await jobs.wait()
        return await list_jobs(jobs.sessions, limit=2)

    listed = run(scenario())

    assert [j["id"] for j in listed] == sorted((j["id"] for j in listed), reverse=True)
    assert len(listed) == 2


# -- HTTP endpoints ------------------------------------------------------------------

class FakeJobs:
    """Endpoint tests only check routing and errors; the job logic is tested above."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.started: list = []

    async def start(self, site=None, devices=None, requested_by=None):
        if self.error:
            raise self.error
        self.started.append((site, devices))
        return 7


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_post_diff_job():
    fake = FakeJobs()
    app.dependency_overrides[get_jobs] = lambda: fake

    response = TestClient(app).post("/diff-jobs", json={"site": "example"})

    assert response.status_code == 202 and response.json() == {"id": 7}
    assert fake.started == [("example", None)]


def test_post_diff_job_rejects_bad_scope():
    app.dependency_overrides[get_jobs] = lambda: FakeJobs(JobError("no active devices in scope"))

    response = TestClient(app).post("/diff-jobs", json={"site": "example"})

    assert response.status_code == 400


def test_jobs_unavailable_without_database():
    app.state.jobs = None

    assert TestClient(app).get("/diff-jobs").status_code == 503
