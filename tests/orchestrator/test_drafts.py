"""Staged changes: intent writes saved in a change set are tried on a copy of NetBox, recorded,
and applied to the live NetBox only when asked (at the window's start)."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from orchestrator import db, drafts
from orchestrator.app import app, current_account, get_accounts, get_deploys, get_drafts, get_netbox
from orchestrator.auth import Accounts
from orchestrator.deploys import DeployJobs
from orchestrator.memory_netbox import FakeNetBox
from seed.model import load
from tests.helpers import seed
from tests import pg

pg.require()
ROOT = Path(__file__).resolve().parents[2]
WEB = {"site": "example", "name": "web", "vni": 10500, "bd_id": 500}


@pytest.fixture
def setup(database_url):
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    nb = FakeNetBox()
    asyncio.run(seed(nb, load(ROOT / "tests" / "fixtures" / "example-small.yaml")))
    accounts = Accounts(sessions)
    asyncio.run(accounts.create_user("tester", "a-long-password-1", role="operator"))
    store = drafts.Drafts(sessions, nb, app)
    saved = getattr(app.state, "netbox", None)
    app.state.netbox = nb   # get_netbox itself must run: it answers a replay's copy
    app.dependency_overrides.pop(get_netbox, None)
    tester = SimpleNamespace(username="tester", role="operator", fabrics=None)
    app.dependency_overrides.update({
        get_deploys: lambda: DeployJobs(sessions, None, nb, None), get_accounts: lambda: accounts,
        get_drafts: lambda: store,
        current_account: lambda: (r.account if (r := drafts.REPLAY.get()) else tester),
    })
    yield SimpleNamespace(sessions=sessions, nb=nb, store=store, client=TestClient(app))
    app.state.netbox = saved
    for dep in (get_deploys, get_accounts, get_drafts):
        app.dependency_overrides.pop(dep, None)


def staged(client, change_set, method, path, **kwargs):
    return client.request(method, path, headers={drafts.HEADER: str(change_set)}, **kwargs)


def audit_actions(sessions):
    async def go():
        async with sessions() as session:
            return [r.action for r in (await session.scalars(select(db.AuditEvent))).all() if r.action != "user.create"]
    return asyncio.run(go())


def test_a_change_set_is_tried_on_a_copy_and_applied_only_when_asked(setup):
    c, nb = setup.client, setup.nb
    change_set = c.post("/fabrics/example/change-sets").json()["id"]

    first = staged(c, change_set, "POST", "/segments", json=WEB)
    # The attachment needs the segment, which only the copy has.
    second = staged(c, change_set, "POST", "/segments/example/10500/attachments",
                    json={"device": "leaf1", "interface": "10GE1/0/3"})

    assert (first.status_code, first.json()["step"]) == (202, 1)
    assert (second.status_code, second.json()["step"]) == (202, 2), second.text
    assert c.get("/segments", params={"site": "example"}).json() == []   # live intent untouched
    assert [s["path"] for s in c.get(f"/fabrics/example/change-sets/{change_set}").json()["steps"]] == \
        ["/segments", "/segments/example/10500/attachments"]
    assert audit_actions(setup.sessions) == []   # nothing changed yet

    asyncio.run(setup.store.apply(change_set))

    segment = c.get("/segments", params={"site": "example"}).json()
    assert [s["name"] for s in segment] == ["web"]
    assert audit_actions(setup.sessions) == ["segment.create", "segment.attach"]


def test_a_refused_step_is_not_recorded(setup):
    c = setup.client
    change_set = c.post("/fabrics/example/change-sets").json()["id"]
    staged(c, change_set, "POST", "/segments", json=WEB)

    again = staged(c, change_set, "POST", "/segments", json={**WEB, "name": "web2"})   # VNI taken in the copy
    elsewhere = staged(c, change_set, "POST", "/segments", json={**WEB, "site": "east"})

    assert again.status_code == 400 and "10500" in again.json()["detail"]
    assert elsewhere.status_code == 400 and "for fabric example, not east" in elsewhere.json()["detail"]
    assert len(c.get(f"/fabrics/example/change-sets/{change_set}").json()["steps"]) == 1


def test_writes_that_cannot_wait_are_refused(setup):
    change_set = setup.client.post("/fabrics/example/change-sets").json()["id"]

    response = staged(setup.client, change_set, "POST", "/routing/example/devices/leaf1/import")

    assert response.status_code == 400 and "cannot be saved for a window" in response.json()["detail"]


def test_staging_goes_on_during_a_rollout_and_a_discarded_set_takes_nothing(setup):
    c = setup.client

    async def rolling():
        async with setup.sessions() as session:
            session.add(db.Job(kind="deploy", status="deploying", requested_by="alice", site="example"))
            await session.commit()
    asyncio.run(rolling())
    change_set = c.post("/fabrics/example/change-sets").json()["id"]

    during = staged(c, change_set, "POST", "/segments", json=WEB)
    live = c.post("/segments", json={**WEB, "name": "db", "vni": 10600, "bd_id": 600})
    discarded = c.delete(f"/fabrics/example/change-sets/{change_set}")
    after = staged(c, change_set, "POST", "/segments", json={**WEB, "name": "app", "vni": 10700, "bd_id": 700})

    assert during.status_code == 202 and live.status_code == 409   # only the live NetBox is frozen
    assert discarded.json()["status"] == "discarded"
    assert after.status_code == 409 and "discarded" in after.json()["detail"]


def test_draft_mode_reads_the_copy_and_refuses_other_live_writes(setup):
    c = setup.client
    change_set = c.post("/fabrics/example/change-sets").json()["id"]
    staged(c, change_set, "POST", "/segments", json=WEB)

    in_draft = staged(c, change_set, "GET", "/segments", params={"site": "example"})
    live = c.get("/segments", params={"site": "example"})
    jobs_list = staged(c, change_set, "GET", "/fabrics/example/change-sets")
    location = staged(c, change_set, "PUT", "/devices/leaf1/location", json={"location": None})

    assert [s["name"] for s in in_draft.json()] == ["web"] and live.json() == []
    assert jobs_list.status_code == 200
    assert location.status_code == 400 and "go back to live" in location.json()["detail"]
