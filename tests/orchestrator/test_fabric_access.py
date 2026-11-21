"""Accounts limited to fabrics: admins and approvers reach every fabric; a limited viewer or
operator sees nothing of the others, and approves only where an admin let them."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from orchestrator import db
from orchestrator.access import Access
from orchestrator.app import app, current_account, current_user, get_accounts, get_deploys, get_netbox
from orchestrator.auth import Accounts
from tests.collector.test_collector import example_netbox
from tests.helpers import seed
from tests.orchestrator.test_segments import second_dc


def run(coro):
    return asyncio.run(coro)


def test_access_rules():
    assert not Access("approver", None).limited and Access("approver").may_approve("example")
    op = Access("operator", {"example": True, "dc2": False})
    assert op.sees("example") and op.sees("dc2") and not op.sees("east") and not op.sees(None)
    assert op.may_approve("example") and not op.may_approve("dc2")
    assert not Access("operator").may_approve("example")                     # unlimited operators: as before
    assert not Access.of(SimpleNamespace(role="approver", fabrics={"example": False})).limited


def test_limits_are_kept_for_viewers_and_operators_only(database_url):
    accounts = Accounts(db.make_sessionmaker(db.make_engine(database_url, pooled=False)))
    run(accounts.create_user("ann", "ann-password", "operator"))
    run(accounts.set_fabrics("ann", {"example": True}, actor="root"))
    with pytest.raises(ValueError, match="at least one fabric"):
        run(accounts.set_fabrics("ann", {}))
    run(accounts.set_role("ann", "viewer"))                               # viewers never approve
    assert next(u for u in run(accounts.list_users()) if u["username"] == "ann")["fabrics"] == {"example": False}
    with pytest.raises(ValueError, match="cannot approve"):
        run(accounts.set_fabrics("ann", {"example": True}))
    run(accounts.set_role("ann", "approver"))                             # every fabric again
    assert next(u for u in run(accounts.list_users()) if u["username"] == "ann")["fabrics"] is None
    with pytest.raises(ValueError, match="reaches every fabric"):
        run(accounts.set_fabrics("ann", {"example": False}))


class FakeDeploys:
    def __init__(self, sessions):
        self.sessions, self.approved = sessions, []

    async def approve(self, job_id, user, window=None):
        self.approved.append((job_id, user))


@pytest.fixture
def limited(database_url):
    nb = example_netbox()
    run(seed(nb, second_dc()))                                           # fabrics example and dc2
    sessions = db.make_sessionmaker(db.make_engine(database_url, pooled=False))
    deploys = FakeDeploys(sessions)
    app.dependency_overrides.update({
        get_netbox: lambda: nb, get_deploys: lambda: deploys, get_accounts: lambda: Accounts(sessions),
        current_user: lambda: "op",
        current_account: lambda: SimpleNamespace(username="op", role="operator", fabrics={"example": True}),
    })
    yield TestClient(app, raise_server_exceptions=False, base_url="https://testserver"), sessions, deploys
    for dep in (get_netbox, get_deploys, get_accounts):
        app.dependency_overrides.pop(dep, None)


def test_a_limited_account_sees_only_its_fabrics(limited):
    client = limited[0]
    assert [f["site"] for f in client.get("/fabrics").json()] == ["example"]
    assert {d["site"] for d in client.get("/devices").json()} == {"example"}
    assert client.get("/auth/me").json()["fabrics"] == {"example": True}
    assert client.get("/segments", params={"site": "dc2"}).status_code == 403
    assert client.get("/fabrics/dc2").status_code == 403
    assert client.get("/devices/dc2-leaf1/ports").status_code == 404
    assert client.get("/devices/leaf1/ports").status_code == 200
    assert client.get("/topology").status_code == 400                  # choose one of your fabrics
    assert client.post("/segments", json={"site": "dc2", "name": "web", "vni": 10500, "bd_id": 500}).status_code == 403
    assert client.post("/fabrics", json={}).status_code == 403          # creating is for approvers and admins
    assert client.post("/imports", json={"addresses": ["10.0.0.1/24"]}).status_code == 403


def test_a_limited_operator_approves_where_allowed(limited):
    client, sessions, deploys = limited

    async def jobs():
        async with sessions() as session:
            found = [db.Job(kind="deploy", status="diffed", site=s) for s in ("example", "dc2")]
            session.add_all(found)
            await session.commit()
            return [j.id for j in found]

    example_job, pip_job = run(jobs())
    assert [j["site"] for j in client.get("/deploy-jobs").json()] == ["example"]
    assert client.post(f"/deploy-jobs/{example_job}/approve").status_code == 200
    assert client.post(f"/deploy-jobs/{pip_job}/approve").status_code == 404   # not even visible
    assert deploys.approved == [(example_job, "op")]
    app.dependency_overrides[current_account] = lambda: SimpleNamespace(username="op", role="operator", fabrics={"example": False})
    assert client.post(f"/deploy-jobs/{example_job}/approve").status_code == 403


def test_a_limited_operator_approves_ztp_where_allowed(limited):
    client = limited[0]
    keys = {"keys": ["ssh-rsa AAAA"]}
    # Past the approval check (leaf2 is not registered for ZTP, so the step itself refuses: 400).
    assert client.post("/devices/leaf2/ztp/approve", json=keys).status_code == 400
    app.dependency_overrides[current_account] = lambda: SimpleNamespace(username="op", role="operator", fabrics={"example": False})
    assert client.post("/devices/leaf2/ztp/approve", json=keys).status_code == 403
    app.dependency_overrides[current_account] = lambda: SimpleNamespace(username="op", role="operator")
    assert client.post("/devices/leaf2/ztp/approve", json=keys).status_code == 403   # unlimited operator
