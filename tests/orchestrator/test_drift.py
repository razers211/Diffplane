"""Scheduled drift checks against a real PostgreSQL."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from orchestrator import db
from orchestrator.app import app, get_drift, get_jobs, get_netbox
from orchestrator.drift import DriftScheduler, drift_report, metrics
from orchestrator.jobs import DiffJobs
from tests.orchestrator.test_diff_jobs import FakeAnnet, example_and_dc2
from tests import pg

pg.require()


def run(coro):
    return asyncio.run(coro)


def setup(database_url, results):
    nb = example_and_dc2()
    jobs = DiffJobs(db.make_sessionmaker(db.make_engine(database_url, pooled=False)), FakeAnnet(results), nb)
    return nb, jobs


def drift_run(jobs: DiffJobs, scheduler: DriftScheduler) -> int | None:
    async def go():
        job_id = await scheduler.run_once()
        await jobs.wait()
        return job_id

    return run(go())


def active(nb):
    return run(nb.find("dcim/devices", status="active", has_primary_ip="true"))


def test_a_manual_change_shows_up_as_out_of_sync(database_url):
    nb, jobs = setup(database_url, {"leaf1": "- snmp-agent sys-info location example-dc-7\n+ snmp-agent sys-info location example-dc\n"})
    scheduler = DriftScheduler(jobs, interval=900)

    job_id = drift_run(jobs, scheduler)

    assert job_id is not None and scheduler.state.last_job_id == job_id
    report = {r["device"]: r for r in run(drift_report(jobs, active(nb)))}
    assert report["leaf1"]["state"] == "out_of_sync"
    assert "example-dc-7" in report["leaf1"]["diff"]
    assert report["leaf1"]["site"] == "example"


def test_the_latest_result_wins_and_errors_are_their_own_state(database_url):
    from orchestrator.annet import AnnetError

    nb, jobs = setup(database_url, {"leaf1": "+ something\n"})
    scheduler = DriftScheduler(jobs, interval=900)
    drift_run(jobs, scheduler)
    jobs.annet.results["leaf1"] = ""
    drift_run(jobs, scheduler)
    assert run(drift_report(jobs, active(nb)))[0]["state"] == "in_sync"

    jobs.annet.results["leaf1"] = AnnetError("unreachable")
    drift_run(jobs, scheduler)
    assert run(drift_report(jobs, active(nb)))[0]["state"] == "error"


def test_never_checked_devices_are_unknown(database_url):
    nb, jobs = setup(database_url, {})
    assert {r["state"] for r in run(drift_report(jobs, active(nb)))} == {"unknown"}


def test_skipped_while_a_deploy_rolls_out(database_url):
    nb, jobs = setup(database_url, {})

    async def deploying():
        async with jobs.sessions() as session:
            job = db.Job(kind="deploy", status="deploying", requested_by="alice")
            session.add(job)
            await session.commit()
            return job.id

    job_id = run(deploying())
    scheduler = DriftScheduler(jobs, interval=900)
    assert drift_run(jobs, scheduler) is None
    assert scheduler.state.last_skip == f"deploy #{job_id} is rolling out"
    assert jobs.annet.calls == []


def test_drift_jobs_are_diff_jobs_but_deploys_are_not(database_url):
    from orchestrator.jobs import get_job, list_jobs

    nb, jobs = setup(database_url, {})
    job_id = drift_run(jobs, DriftScheduler(jobs, interval=900))

    async def add_deploy():
        async with jobs.sessions() as session:
            job = db.Job(kind="deploy", status="verified", requested_by="alice")
            session.add(job)
            await session.commit()
            return job.id

    deploy_id = run(add_deploy())
    listed = run(list_jobs(jobs.sessions, 20))
    assert [j["id"] for j in listed] == [job_id]
    assert listed[0]["kind"] == "drift" and listed[0]["requested_by"] == "scheduler"
    assert run(get_job(jobs.sessions, deploy_id)) is None


def test_metrics_export_drift():
    report = [
        {"device": "leaf1", "site": "example", "state": "out_of_sync"},
        {"device": "leaf2", "site": "example", "state": "in_sync"},
    ]
    text = metrics(report, None).decode()
    assert 'fabric_drift_device_state{device="leaf1",site="example",state="out_of_sync"} 1.0' in text
    assert 'fabric_drift_device_state{device="leaf1",site="example",state="in_sync"} 0.0' in text
    assert "fabric_drift_out_of_sync_devices 1.0" in text


def test_a_skipped_run_is_retried_soon(database_url):
    """A run skipped at start-up must not wait a whole interval (FabricDriftCheckStopped)."""
    nb, jobs = setup(database_url, {})
    scheduler = DriftScheduler(jobs, interval=900)

    assert scheduler.next_delay(None) == 60
    assert scheduler.next_delay(1) == 900
    assert DriftScheduler(jobs, interval=30).next_delay(None) == 30


def test_last_run_is_absent_until_a_run_started():
    """An unset gauge would export 0 (1970) and fire FabricDriftCheckStopped at once."""
    from orchestrator.drift import SchedulerState

    assert "fabric_drift_last_run_timestamp_seconds" not in metrics([], SchedulerState(interval=900)).decode()
    started = metrics([], SchedulerState(interval=900, last_started=1_791_000_000.0)).decode()
    assert "fabric_drift_last_run_timestamp_seconds 1.791e+09" in started
    assert "fabric_drift_interval_seconds 900.0" in started


def test_http_drift_and_metrics(database_url, monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "scrape-token")
    nb, jobs = setup(database_url, {"leaf1": "+ x\n"})
    scheduler = DriftScheduler(jobs, interval=900)
    drift_run(jobs, scheduler)
    app.dependency_overrides.update({get_jobs: lambda: jobs, get_netbox: lambda: nb, get_drift: lambda: scheduler})
    try:
        client = TestClient(app)
        body = client.get("/drift").json()
        assert body["scheduler"]["interval_seconds"] == 900
        assert body["devices"][0]["state"] == "out_of_sync"
        assert client.get("/metrics").status_code == 401
        response = client.get("/metrics", headers={"Authorization": "Bearer scrape-token"})
        assert response.status_code == 200
        assert "fabric_drift_out_of_sync_devices 1.0" in response.text
        assert "+ x" not in response.text   # never diffs in the metrics
    finally:
        for dep in (get_jobs, get_netbox, get_drift):
            app.dependency_overrides.pop(dep, None)
