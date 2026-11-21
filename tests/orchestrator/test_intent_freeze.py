"""Intent is frozen while a deploy rolls out: `annet deploy` regenerates from NetBox, so a
change made mid-rollout would otherwise be deployed without approval (found in testing)."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import seed.__main__ as seed_cli
from orchestrator import db
from orchestrator.app import app, get_accounts, get_deploys, get_netbox
from orchestrator.auth import Accounts
from orchestrator.deploys import DeployJobs, rolling_out
from seed.loader import SeedError
from seed.model import load
from tests.seed.fake_netbox import FakeNetBox
from tests.helpers import seed
from tests import pg

pg.require()
ROOT = Path(__file__).resolve().parents[2]


def set_job(sessions, status: str) -> int:
    async def go():
        async with sessions() as session:
            job = db.Job(kind="deploy", status=status, requested_by="alice")
            session.add(job)
            await session.commit()
            return job.id

    return asyncio.run(go())


@pytest.fixture
def setup(database_url):
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    nb = FakeNetBox()
    asyncio.run(seed(nb, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    jobs = DeployJobs(sessions, None, nb, None)
    app.dependency_overrides.update({
        get_netbox: lambda: nb, get_deploys: lambda: jobs, get_accounts: lambda: Accounts(sessions),
    })
    yield sessions
    for dep in (get_netbox, get_deploys, get_accounts):
        app.dependency_overrides.pop(dep, None)


@pytest.mark.parametrize("status, frozen", [("approved", True), ("deploying", True), ("diffed", False),
                                            ("verified", False), ("failed", False)])
def test_segment_changes_are_refused_during_a_rollout(setup, status, frozen):
    job_id = set_job(setup, status)

    response = TestClient(app).post("/segments", json={"site": "example", "name": "web", "vni": 10500, "bd_id": 500})

    if frozen:
        assert response.status_code == 409 and f"deploy #{job_id} is rolling out" in response.json()["detail"]
    else:
        assert response.status_code == 201


def test_reads_stay_available_during_a_rollout(setup):
    set_job(setup, "deploying")

    assert TestClient(app).get("/segments", params={"site": "example"}).status_code == 200


def test_seed_cli_refuses_during_a_rollout(setup, database_url, monkeypatch):
    job_id = set_job(setup, "deploying")
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert asyncio.run(seed_cli.rollout_in_progress()) == job_id
    with pytest.raises(SeedError, match=f"deploy #{job_id} is rolling out"):
        asyncio.run(seed_cli.seed(str(ROOT / "tests" / "fixtures" / "example-small.yaml")))


def test_rolling_out_helper(setup):
    assert asyncio.run(rolling_out(setup)) is None
    job_id = set_job(setup, "approved")
    assert asyncio.run(rolling_out(setup)) == job_id


def test_every_intent_write_route_is_frozen_during_a_rollout():
    from fastapi.routing import APIRoute

    from orchestrator.app import _intent_unfrozen, api_routes

    writes = [r for r in api_routes() if isinstance(r, APIRoute) and r.methods & {"POST", "PUT", "PATCH", "DELETE"}
              and r.path.split("/")[1] in ("segments", "services")]
    assert {r.path for r in writes} >= {"/segments", "/services", "/segments/{site}/{vni}/routing"}
    for route in writes:
        assert any(d.dependency is _intent_unfrozen for d in route.dependencies), route.path


def test_trunk_and_lifecycle_routes_are_frozen_too():
    """Trunks, activation, decommission, adding and removing switches change
    what deploys reach."""
    from fastapi.routing import APIRoute

    from orchestrator.app import _intent_unfrozen, api_routes

    paths = {"/devices/{name}/trunks", "/devices/{name}/trunks/{trunk}", "/devices/{name}/management", "/devices/{name}",
             "/fabrics/{site}/spines", "/fabrics/{site}/leaf-pairs", "/fabrics/{site}/switches"}
    routes = [r for r in api_routes() if isinstance(r, APIRoute) and r.path in paths
              and r.methods & {"POST", "PUT", "DELETE"}]
    assert len(routes) == 8
    for route in routes:
        assert any(d.dependency is _intent_unfrozen for d in route.dependencies), route.path
